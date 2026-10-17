from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_CEILING, Decimal
from hashlib import sha256
from typing import Any, Literal, Protocol, cast
from uuid import UUID, uuid5

from pydantic import BaseModel, ValidationError
from uagents import Context
from uagents import Protocol as AgentProtocol
from uagents_core.contrib.protocols.chat import ChatMessage, MetadataContent, TextContent
from uagents_core.types import DeliveryStatus

from supplymesh.agents.connectivity import FetchConnectivityCoordinator, MessageContext
from supplymesh.agents.transport import (
    AcknowledgmentComparisonTransport,
    AcknowledgmentReceivedTransport,
    ChangeOrderSubmittedTransport,
    ChangeResponseReceivedTransport,
    CounterproposalExecutionTransport,
    DemandPlanningTransport,
    ProcurementPlansReadyTransport,
    PurchaseOrderExecutionTransport,
    PurchaseOrderSubmittedTransport,
    SourcingRequestTransport,
    SpecialistFailureTransport,
    SupplierQuotesCollectedTransport,
    WorkflowStatusResponseTransport,
)
from supplymesh.cards.completion import agent_activity_detail, completion_detail
from supplymesh.cards.exception import exception_review
from supplymesh.cards.inventory import inventory_assessment_detail
from supplymesh.cards.plans import plan_approval_review, procurement_plan_carousel
from supplymesh.cards.procurement import (
    counterproposal_approval_review,
    counterproposal_form,
    purchase_order_batch_review,
)
from supplymesh.cards.renderer import CardSelection
from supplymesh.domain.models import InventoryAssessment, TentativeReservation
from supplymesh.domain.procurement import (
    ApprovalRecord,
    Counterproposal,
    CounterproposalLine,
    PurchaseOrder,
)
from supplymesh.domain.sourcing import ProcurementPlan
from supplymesh.persistence.audit_repository import AuditRepository
from supplymesh.persistence.workflow_repository import PendingOutbound, WorkflowSnapshot
from supplymesh.protocols.common import WorkflowStatusResponse
from supplymesh.protocols.exceptions import AcknowledgmentComparisonResult
from supplymesh.protocols.planning import DemandPlanningRequest, InventoryShortageReport
from supplymesh.protocols.procurement import (
    AcknowledgmentReceived,
    ChangeOrderSubmitted,
    ChangeResponseReceived,
    CounterproposalExecutionRequest,
    PurchaseOrderExecutionRequest,
    PurchaseOrderSubmitted,
)
from supplymesh.protocols.sourcing import (
    ProcurementPlansReady,
    SourcingRequest,
    SupplierQuotesCollected,
)
from supplymesh.services.order_factory import purchase_orders_from_plan
from supplymesh.workflows.state import ProcurementWorkflowState


class OrchestrationStore(Protocol):
    def snapshot(self, workflow_id: UUID) -> WorkflowSnapshot | None: ...

    def find_active_by_conversation(self, conversation_id: str) -> WorkflowSnapshot | None: ...

    def update(
        self,
        workflow_id: UUID,
        *,
        status: str | None = None,
        updates: dict[str, Any] | None = None,
        inbound_message_id: UUID | None = None,
    ) -> bool: ...

    def record_outbound(
        self,
        workflow_id: UUID,
        *,
        message_id: UUID,
        destination: str,
        message_type: str,
        status: str,
        payload_json: str | None = None,
    ) -> None: ...

    def pending_outbound(self) -> tuple[PendingOutbound, ...]: ...

    def save_approval(
        self,
        *,
        workflow_id: UUID,
        decision_type: Literal[
            "SELECT_PLAN", "SEND_PURCHASE_ORDER", "ACCEPT_EXCEPTION", "SEND_COUNTERPROPOSAL"
        ],
        target_type: str,
        target_id: str,
        target_version: int,
        actor: str,
        payload: BaseModel | dict[str, Any],
        reason: str | None = None,
    ) -> ApprovalRecord: ...


class ReservationStore(Protocol):
    def save_tentative_reservations(
        self,
        workflow_id: UUID,
        reservations: tuple[TentativeReservation, ...],
    ) -> None: ...

    def transition_reservations(
        self, workflow_id: UUID, *, status: Literal["COMMITTED", "RELEASED"]
    ) -> int: ...


@dataclass(frozen=True)
class OrchestrationCoordinator(FetchConnectivityCoordinator):
    sourcing_address: str = ""
    procurement_address: str = ""
    acknowledgment_address: str = ""
    transition_graph: Any | None = None
    audit: AuditRepository | None = None
    reservation_store: ReservationStore | None = None

    @property
    def routing_store(self) -> OrchestrationStore:
        return cast(OrchestrationStore, self.workflows)

    def _record_inventory_delivery(self, request: DemandPlanningRequest, status: str) -> None:
        self.routing_store.record_outbound(
            request.workflow_id,
            message_id=request.message_id,
            destination=self.inventory_address,
            message_type=request.message_type,
            status=status,
            payload_json=request.model_dump_json(),
        )
        if self.audit is not None:
            self.audit.log_message(
                workflow_id=request.workflow_id,
                message_id=request.message_id,
                sender="manufacturing-orchestrator",
                recipient=self.inventory_address,
                message_type=request.message_type,
                payload_hash=sha256(request.model_dump_json().encode()).hexdigest(),
                status=status,
                sent_at=request.sent_at,
                received_at=datetime.now(UTC) if status != "PENDING" else None,
            )

    async def complete_inventory(
        self, ctx: MessageContext, report: InventoryShortageReport
    ) -> None:
        session = self._validated_session(report)
        self._audit_received(report)
        assessment = report.assessment
        snapshot = self._snapshot(report.workflow_id)
        event = "inventory_shortage" if assessment.has_shortages else "inventory_sufficient"
        next_status = self._advance(snapshot, event)
        if self.reservation_store is not None:
            self.reservation_store.save_tentative_reservations(
                report.workflow_id, report.tentative_reservations
            )
            if next_status == "COMPLETED":
                self._transition_reservations(report.workflow_id, "COMMITTED")
        accepted = self.routing_store.update(
            report.workflow_id,
            status=next_status,
            updates={
                "inventory_result": {
                    "assessment": assessment.model_dump(mode="json"),
                    "has_shortages": assessment.has_shortages,
                    "report_message_id": str(report.message_id),
                }
            },
            inbound_message_id=report.message_id,
        )
        if not accepted:
            return
        card = inventory_assessment_detail(assessment)
        shortage_count = sum(1 for line in assessment.lines if line.shortage > 0)
        await self._send_chat(
            ctx,
            session.conversation_id,
            (
                "Inventory planning is complete. Existing reservations and safety stock remain "
                f"protected; {shortage_count} component line(s) require sourcing."
            ),
            card.metadata(),
        )
        if assessment.has_shortages:
            await self._dispatch_sourcing(ctx, snapshot, assessment)
        else:
            await self._send_completion(ctx, self._snapshot(report.workflow_id))

    async def handle_quotes(self, ctx: MessageContext, collected: SupplierQuotesCollected) -> None:
        self._audit_received(collected)
        snapshot = self._validated_reply(
            collected.workflow_id,
            collected.correlation_id,
            collected.reply_to,
            "sourcing_request",
        )
        self.routing_store.update(
            collected.workflow_id,
            updates={
                "quotes": [quote.model_dump(mode="json") for quote in collected.quotes],
                "missing_suppliers": list(collected.missing_suppliers),
                "quotes_message_id": str(collected.message_id),
            },
            inbound_message_id=collected.message_id,
        )
        del ctx, snapshot

    async def handle_plans(self, ctx: MessageContext, ready: ProcurementPlansReady) -> None:
        self._audit_received(ready)
        snapshot = self._snapshot(ready.workflow_id)
        if self._already_processed(snapshot, ready.message_id):
            return
        self._validate_correlation(snapshot, ready.correlation_id)
        if ready.reply_to != snapshot.state.get("quotes_message_id"):
            raise ValueError("plans do not answer the recorded quote collection")
        if not ready.result.plans:
            reason = "; ".join(ready.result.infeasibility_reasons) or "No feasible plan exists."
            await self._fail(ctx, snapshot, "SOURCING_INFEASIBLE", reason, ready.message_id)
            return
        status = self._advance(snapshot, "plans_ready")
        accepted = self.routing_store.update(
            ready.workflow_id,
            status=status,
            updates={
                "plans": [plan.model_dump(mode="json") for plan in ready.result.plans],
                "plans_message_id": str(ready.message_id),
            },
            inbound_message_id=ready.message_id,
        )
        if not accepted:
            return
        await self._send_chat(
            ctx,
            snapshot.conversation_id,
            "Supplier quotations are complete. Review the deterministic procurement plans.",
            procurement_plan_carousel(ready.result.plans).metadata(),
        )

    async def handle_selection(
        self, ctx: MessageContext, *, conversation_id: str, selection: CardSelection
    ) -> bool:
        snapshot = self.routing_store.find_active_by_conversation(conversation_id)
        if snapshot is None:
            return False
        if selection.action == "review_plan":
            await self._review_plan(ctx, snapshot, selection)
            return True
        if selection.action == "approve_plan":
            await self._approve_plan(ctx, snapshot, selection)
            return True
        if selection.action == "approve_and_send_purchase_orders":
            await self._approve_purchase_orders(ctx, snapshot, selection)
            return True
        if selection.action == "cancel" and snapshot.status == "WAITING_FOR_PO_APPROVAL":
            await self._cancel(ctx, snapshot, "purchase_orders_cancelled")
            return True
        if (
            selection.action == "cancel"
            and snapshot.status == "WAITING_FOR_COUNTERPROPOSAL_APPROVAL"
        ):
            await self._cancel(ctx, snapshot, "counterproposal_cancelled")
            return True
        if selection.action in {"accept", "counter", "switch", "split", "cancel"}:
            await self._resolve_exception(ctx, snapshot, selection)
            return True
        if selection.action == "review_counterproposal":
            await self._review_counterproposal(ctx, snapshot, selection)
            return True
        if selection.action == "approve_counterproposal":
            await self._approve_counterproposal(ctx, snapshot, selection)
            return True
        if selection.action == "reject_plan":
            await self._cancel(ctx, snapshot, "plan_rejected")
            return True
        return False

    async def handle_po_submitted(
        self, ctx: MessageContext, submitted: PurchaseOrderSubmitted
    ) -> None:
        self._audit_received(submitted)
        snapshot = self._validated_reply(
            submitted.workflow_id,
            submitted.correlation_id,
            submitted.reply_to,
            "po_execution_request",
        )
        if self._already_processed(snapshot, submitted.message_id):
            return
        submissions = dict(snapshot.state.get("po_submissions", {}))
        submissions[submitted.po_id] = submitted.model_dump(mode="json")
        status = self._advance(snapshot, "purchase_orders_submitted")
        self.routing_store.update(
            submitted.workflow_id,
            status=status,
            updates={"po_submissions": submissions},
            inbound_message_id=submitted.message_id,
        )
        del ctx

    async def handle_acknowledgment(
        self, ctx: MessageContext, acknowledgment: AcknowledgmentReceived
    ) -> None:
        self._audit_received(acknowledgment)
        snapshot = self._snapshot(acknowledgment.workflow_id)
        self._validate_correlation(snapshot, acknowledgment.correlation_id)
        submission_data = snapshot.state.get("po_submissions", {}).get(acknowledgment.po_id)
        if submission_data is None:
            raise ValueError("acknowledgment has no recorded PO submission")
        submission = PurchaseOrderSubmitted.model_validate(submission_data)
        if acknowledgment.reply_to != str(submission.message_id):
            raise ValueError("acknowledgment does not answer the recorded PO submission")
        forwarded = acknowledgment.model_copy(
            update={
                "message_id": uuid5(acknowledgment.message_id, "acknowledgment-agent"),
                "reply_to": str(acknowledgment.message_id),
                "sender_agent": "manufacturing-orchestrator",
                "sent_at": datetime.now(UTC),
            }
        )
        acknowledgments = dict(snapshot.state.get("acknowledgments", {}))
        acknowledgments[acknowledgment.po_id] = forwarded.model_dump(mode="json")
        accepted = self.routing_store.update(
            acknowledgment.workflow_id,
            updates={"acknowledgments": acknowledgments},
            inbound_message_id=acknowledgment.message_id,
        )
        if not accepted:
            return
        await self._send_specialist(
            ctx,
            snapshot,
            self.acknowledgment_address,
            AcknowledgmentReceivedTransport.from_domain(forwarded),
            forwarded.message_id,
            "AcknowledgmentReceived",
        )

    async def handle_change_submitted(
        self, ctx: MessageContext, submitted: ChangeOrderSubmitted
    ) -> None:
        self._audit_received(submitted)
        snapshot = self._validated_reply(
            submitted.workflow_id,
            submitted.correlation_id,
            submitted.reply_to,
            "counterproposal_request",
        )
        if self._already_processed(snapshot, submitted.message_id):
            return
        status = self._advance(snapshot, "counterproposal_submitted")
        self.routing_store.update(
            submitted.workflow_id,
            status=status,
            updates={"change_submission": submitted.model_dump(mode="json")},
            inbound_message_id=submitted.message_id,
        )
        del ctx

    async def handle_change_response(
        self, ctx: MessageContext, response: ChangeResponseReceived
    ) -> None:
        self._audit_received(response)
        snapshot = self._snapshot(response.workflow_id)
        self._validate_correlation(snapshot, response.correlation_id)
        submission_data = snapshot.state.get("change_submission")
        if not isinstance(submission_data, dict):
            raise ValueError("change response has no recorded change submission")
        submission = ChangeOrderSubmitted.model_validate(submission_data)
        if response.reply_to != str(submission.message_id):
            raise ValueError("change response does not answer the recorded change submission")
        forwarded = response.model_copy(
            update={
                "message_id": uuid5(response.message_id, "acknowledgment-agent"),
                "reply_to": str(response.message_id),
                "sender_agent": "manufacturing-orchestrator",
                "sent_at": datetime.now(UTC),
            }
        )
        accepted = self.routing_store.update(
            response.workflow_id,
            updates={"change_response": forwarded.model_dump(mode="json")},
            inbound_message_id=response.message_id,
        )
        if not accepted:
            return
        await self._send_specialist(
            ctx,
            snapshot,
            self.acknowledgment_address,
            ChangeResponseReceivedTransport.from_domain(forwarded),
            forwarded.message_id,
            "ChangeResponseReceived",
        )

    async def handle_change_status(
        self, ctx: MessageContext, result: WorkflowStatusResponse
    ) -> None:
        self._audit_received(result)
        snapshot = self._snapshot(result.workflow_id)
        if self._already_processed(snapshot, result.message_id):
            return
        self._validate_correlation(snapshot, result.correlation_id)
        response_data = snapshot.state.get("change_response")
        if not isinstance(response_data, dict):
            raise ValueError("change status has no recorded supplier response")
        response = ChangeResponseReceived.model_validate(response_data)
        if result.reply_to != str(response.message_id):
            raise ValueError("change status does not answer the recorded supplier response")
        is_accepted = result.status == "CHANGE_ACCEPTED" and result.summary.get("accepted") is True
        status = self._advance(snapshot, "change_accepted" if is_accepted else "change_rejected")
        if status == "COMPLETED":
            self._transition_reservations(result.workflow_id, "COMMITTED")
        accepted = self.routing_store.update(
            result.workflow_id,
            status=status,
            updates={"change_status": result.model_dump(mode="json")},
            inbound_message_id=result.message_id,
        )
        if not accepted:
            return
        current = self._snapshot(result.workflow_id)
        if status == "COMPLETED":
            await self._send_completion(ctx, current)
        else:
            await self._send_chat(
                ctx,
                current.conversation_id,
                "The supplier rejected part of the counterproposal. Another human decision is "
                "required.",
            )

    async def handle_comparison(
        self, ctx: MessageContext, result: AcknowledgmentComparisonResult
    ) -> None:
        self._audit_received(result)
        snapshot = self._snapshot(result.workflow_id)
        if self._already_processed(snapshot, result.message_id):
            return
        self._validate_correlation(snapshot, result.correlation_id)
        acknowledgment_data = snapshot.state.get("acknowledgments", {}).get(result.po_id)
        if acknowledgment_data is None:
            raise ValueError("comparison has no recorded acknowledgment")
        acknowledgment = AcknowledgmentReceived.model_validate(acknowledgment_data)
        if result.reply_to != str(acknowledgment.message_id):
            raise ValueError("comparison does not answer the recorded acknowledgment")
        comparisons = dict(snapshot.state.get("comparisons", {}))
        comparisons[result.po_id] = result.model_dump(mode="json")
        po_count = len(snapshot.state.get("purchase_orders", []))
        updates: dict[str, Any] = {"comparisons": comparisons}
        status: str | None = None
        if len(comparisons) >= po_count:
            conflicts = [
                AcknowledgmentComparisonResult.model_validate(value)
                for value in comparisons.values()
                if value["comparison"]["status"] != "MATCHED"
            ]
            status = self._advance(
                snapshot,
                "acknowledgments_conflicted" if conflicts else "acknowledgments_matched",
            )
            if status == "COMPLETED":
                self._transition_reservations(result.workflow_id, "COMMITTED")
        accepted = self.routing_store.update(
            result.workflow_id,
            status=status,
            updates=updates,
            inbound_message_id=result.message_id,
        )
        if not accepted or status is None:
            return
        current = self._snapshot(result.workflow_id)
        if status == "COMPLETED":
            await self._send_completion(ctx, current)
            return
        conflict = next(
            AcknowledgmentComparisonResult.model_validate(value)
            for value in comparisons.values()
            if value["comparison"]["status"] != "MATCHED"
        )
        await self._send_chat(
            ctx,
            current.conversation_id,
            "A supplier acknowledgment differs from the approved purchase order. "
            "Human review is required.",
            exception_review(
                conflict.comparison,
                po_id=conflict.po_id,
                po_version=conflict.po_version,
            ).metadata(),
        )

    async def handle_failure(
        self, ctx: MessageContext, failure: SpecialistFailureTransport
    ) -> None:
        snapshot = self._snapshot(UUID(failure.workflow_id))
        self._validate_correlation(snapshot, UUID(failure.correlation_id))
        pending_ids = {
            str(value.get("message_id"))
            for key, value in snapshot.state.items()
            if key.endswith("request") and isinstance(value, dict)
        }
        if failure.reply_to not in pending_ids:
            raise ValueError("specialist failure does not answer a pending request")
        await self._fail(
            ctx,
            snapshot,
            failure.code,
            failure.message,
            UUID(failure.reply_to),
        )

    async def _dispatch_sourcing(
        self,
        ctx: MessageContext,
        snapshot: WorkflowSnapshot,
        assessment: InventoryAssessment,
    ) -> None:
        shortages: dict[str, int] = {}
        required_by: dict[str, str] = {}
        for line in assessment.lines:
            if line.shortage <= 0:
                continue
            integral = int(line.shortage.to_integral_value(rounding=ROUND_CEILING))
            shortages[line.component_sku] = integral
            required_by[line.component_sku] = assessment.required_by.isoformat()
        request = SourcingRequest(
            correlation_id=UUID(snapshot.state["correlation_id"]),
            workflow_id=snapshot.workflow_id,
            sender_agent="manufacturing-orchestrator",
            shortages=shortages,
            required_by=required_by,
            currency=snapshot.state.get("currency", "USD"),
        )
        self.routing_store.update(
            snapshot.workflow_id,
            updates={"sourcing_request": request.model_dump(mode="json")},
        )
        await self._send_specialist(
            ctx,
            snapshot,
            self.sourcing_address,
            SourcingRequestTransport.from_domain(request),
            request.message_id,
            "SourcingRequest",
        )

    async def _review_plan(
        self, ctx: MessageContext, snapshot: WorkflowSnapshot, selection: CardSelection
    ) -> None:
        self._require_status(snapshot, "WAITING_FOR_PLAN_APPROVAL")
        plan = self._plan(snapshot, selection.target_id)
        self.routing_store.update(
            snapshot.workflow_id,
            updates={"selected_plan_id": plan.plan_id, "selected_plan_version": 1},
        )
        await self._send_chat(
            ctx,
            snapshot.conversation_id,
            "Review the selected plan. Approval only selects it; purchase orders require a "
            "separate approval.",
            plan_approval_review(plan, 1).metadata(),
        )

    async def _approve_plan(
        self, ctx: MessageContext, snapshot: WorkflowSnapshot, selection: CardSelection
    ) -> None:
        self._require_status(snapshot, "WAITING_FOR_PLAN_APPROVAL")
        if selection.target_id != snapshot.state.get(
            "selected_plan_id"
        ) or selection.target_version != snapshot.state.get("selected_plan_version"):
            raise ValueError("plan approval is stale or targets a different plan")
        plan = self._plan(snapshot, selection.target_id)
        self.routing_store.save_approval(
            workflow_id=snapshot.workflow_id,
            decision_type="SELECT_PLAN",
            target_type="procurement_plan",
            target_id=plan.plan_id,
            target_version=1,
            actor=snapshot.conversation_id,
            payload=plan,
        )
        purchase_orders = purchase_orders_from_plan(snapshot.workflow_id, plan)
        batch_id = str(uuid5(snapshot.workflow_id, f"purchase-order-batch:{plan.plan_id}"))
        status = self._advance(snapshot, "plan_approved")
        self.routing_store.update(
            snapshot.workflow_id,
            status=status,
            updates={
                "purchase_orders": [po.model_dump(mode="json") for po in purchase_orders],
                "purchase_order_batch_id": batch_id,
                "purchase_order_batch_version": 1,
            },
        )
        await self._send_chat(
            ctx,
            snapshot.conversation_id,
            "The plan is selected. Review and separately approve the purchase orders before any "
            "supplier submission.",
            purchase_order_batch_review(
                purchase_orders, batch_id=batch_id, batch_version=1
            ).metadata(),
        )

    async def _approve_purchase_orders(
        self, ctx: MessageContext, snapshot: WorkflowSnapshot, selection: CardSelection
    ) -> None:
        self._require_status(snapshot, "WAITING_FOR_PO_APPROVAL")
        if selection.target_id != snapshot.state.get(
            "purchase_order_batch_id"
        ) or selection.target_version != snapshot.state.get("purchase_order_batch_version"):
            raise ValueError("purchase-order approval is stale or targets a different batch")
        purchase_orders = tuple(
            PurchaseOrder.model_validate(value) for value in snapshot.state["purchase_orders"]
        )
        approvals = tuple(
            self.routing_store.save_approval(
                workflow_id=snapshot.workflow_id,
                decision_type="SEND_PURCHASE_ORDER",
                target_type="purchase_order",
                target_id=str(po.po_id),
                target_version=po.version,
                actor=snapshot.conversation_id,
                payload=po,
            )
            for po in purchase_orders
        )
        request = PurchaseOrderExecutionRequest(
            correlation_id=UUID(snapshot.state["correlation_id"]),
            workflow_id=snapshot.workflow_id,
            sender_agent="manufacturing-orchestrator",
            purchase_orders=purchase_orders,
            approval_id=str(approvals[0].approval_id),
            approval_ids=tuple(str(item.approval_id) for item in approvals),
        )
        status = self._advance(snapshot, "purchase_orders_approved")
        self.routing_store.update(
            snapshot.workflow_id,
            status=status,
            updates={"po_execution_request": request.model_dump(mode="json")},
        )
        await self._send_specialist(
            ctx,
            snapshot,
            self.procurement_address,
            PurchaseOrderExecutionTransport.from_domain(request),
            request.message_id,
            "PurchaseOrderExecutionRequest",
        )

    async def _resolve_exception(
        self, ctx: MessageContext, snapshot: WorkflowSnapshot, selection: CardSelection
    ) -> None:
        self._require_status(snapshot, "WAITING_FOR_EXCEPTION_DECISION")
        comparisons = snapshot.state.get("comparisons", {})
        if selection.target_id not in comparisons:
            raise ValueError("exception decision targets an unknown purchase order")
        comparison = AcknowledgmentComparisonResult.model_validate(comparisons[selection.target_id])
        if selection.target_version != comparison.po_version:
            raise ValueError("exception decision targets a stale purchase-order version")
        if selection.action == "counter":
            self.routing_store.update(
                snapshot.workflow_id,
                updates={"counterproposal_po_id": comparison.po_id},
            )
            await self._send_chat(
                ctx,
                snapshot.conversation_id,
                "Enter the acceptable quantity, maximum unit price, and required date.",
                counterproposal_form(
                    po_id=comparison.po_id, po_version=comparison.po_version
                ).metadata(),
            )
            return
        if selection.action in {"switch", "split"}:
            status = self._advance(snapshot, "resourcing_requested")
            self.routing_store.update(
                snapshot.workflow_id,
                status=status,
                updates={"decision": selection.action},
            )
            assessment = InventoryAssessment.model_validate(
                snapshot.state["inventory_result"]["assessment"]
            )
            await self._dispatch_sourcing(ctx, self._snapshot(snapshot.workflow_id), assessment)
            return
        event = "exception_accepted" if selection.action == "accept" else "exception_cancelled"
        status = self._advance(snapshot, event)
        self._transition_reservations(
            snapshot.workflow_id,
            "COMMITTED" if status == "COMPLETED" else "RELEASED",
        )
        self.routing_store.update(snapshot.workflow_id, status=status, updates={"decision": event})
        if status == "COMPLETED":
            await self._send_completion(ctx, self._snapshot(snapshot.workflow_id))
        else:
            await self._send_chat(
                ctx,
                snapshot.conversation_id,
                "The procurement workflow was cancelled. No further supplier action will be taken.",
            )

    async def _review_counterproposal(
        self, ctx: MessageContext, snapshot: WorkflowSnapshot, selection: CardSelection
    ) -> None:
        self._require_status(snapshot, "WAITING_FOR_EXCEPTION_DECISION")
        po_id = snapshot.state.get("counterproposal_po_id")
        if selection.target_id not in {None, po_id}:
            raise ValueError("counterproposal targets a different purchase order")
        po = self._purchase_order(snapshot, po_id)
        values = selection.values
        quantity = Decimal(str(values.get("quantity", po.lines[0].quantity)))
        maximum_price = Decimal(str(values.get("maximum_price", po.lines[0].unit_price)))
        required_date = date.fromisoformat(
            str(values.get("required_date", po.lines[0].requested_date.isoformat()))
        )
        counterproposal = Counterproposal(
            workflow_id=snapshot.workflow_id,
            po_id=po.po_id,
            po_number=po.po_number,
            current_po_version=po.version,
            change_version=po.version + 1,
            supplier_id=po.supplier_id,
            currency=po.currency,
            lines=tuple(
                CounterproposalLine(
                    line_number=line.line_number,
                    component_sku=line.component_sku,
                    quantity=quantity if len(po.lines) == 1 else line.quantity,
                    unit=line.unit,
                    maximum_unit_price=(maximum_price if len(po.lines) == 1 else line.unit_price),
                    requested_date=(required_date if len(po.lines) == 1 else line.requested_date),
                )
                for line in po.lines
            ),
        )
        status = self._advance(snapshot, "counterproposal_prepared")
        self.routing_store.update(
            snapshot.workflow_id,
            status=status,
            updates={"counterproposal": counterproposal.model_dump(mode="json")},
        )
        await self._send_chat(
            ctx,
            snapshot.conversation_id,
            "Review the counterproposal. It will not be sent without this separate approval.",
            counterproposal_approval_review(
                po_id=str(po.po_id),
                po_version=po.version,
                values=counterproposal.model_dump(mode="json"),
            ).metadata(),
        )

    async def _approve_counterproposal(
        self, ctx: MessageContext, snapshot: WorkflowSnapshot, selection: CardSelection
    ) -> None:
        self._require_status(snapshot, "WAITING_FOR_COUNTERPROPOSAL_APPROVAL")
        counterproposal = Counterproposal.model_validate(snapshot.state["counterproposal"])
        if (
            selection.target_id != str(counterproposal.po_id)
            or selection.target_version != counterproposal.current_po_version
        ):
            raise ValueError("counterproposal approval is stale or targets a different PO")
        approval = self.routing_store.save_approval(
            workflow_id=snapshot.workflow_id,
            decision_type="SEND_COUNTERPROPOSAL",
            target_type="purchase_order",
            target_id=str(counterproposal.po_id),
            target_version=counterproposal.current_po_version,
            actor=snapshot.conversation_id,
            payload=counterproposal,
        )
        request = CounterproposalExecutionRequest(
            correlation_id=UUID(snapshot.state["correlation_id"]),
            workflow_id=snapshot.workflow_id,
            sender_agent="manufacturing-orchestrator",
            counterproposal=counterproposal,
            approval_id=str(approval.approval_id),
        )
        status = self._advance(snapshot, "counterproposal_approved")
        self.routing_store.update(
            snapshot.workflow_id,
            status=status,
            updates={"counterproposal_request": request.model_dump(mode="json")},
        )
        await self._send_specialist(
            ctx,
            snapshot,
            self.procurement_address,
            CounterproposalExecutionTransport.from_domain(request),
            request.message_id,
            "CounterproposalExecutionRequest",
        )

    async def _cancel(self, ctx: MessageContext, snapshot: WorkflowSnapshot, event: str) -> None:
        status = self._advance(snapshot, event)
        self._transition_reservations(snapshot.workflow_id, "RELEASED")
        self.routing_store.update(snapshot.workflow_id, status=status)
        await self._send_chat(
            ctx,
            snapshot.conversation_id,
            "The procurement workflow was cancelled. No supplier order was sent.",
        )

    async def _send_completion(self, ctx: MessageContext, snapshot: WorkflowSnapshot) -> None:
        plans = [ProcurementPlan.model_validate(value) for value in snapshot.state.get("plans", [])]
        selected = next(
            (plan for plan in plans if plan.plan_id == snapshot.state.get("selected_plan_id")),
            None,
        )
        purchase_orders = [
            PurchaseOrder.model_validate(value)
            for value in snapshot.state.get("purchase_orders", [])
        ]
        card = completion_detail(
            workflow_id=str(snapshot.workflow_id),
            total_cost_minor=selected.total_cost_minor if selected else 0,
            currency=selected.currency if selected else snapshot.state.get("currency", "USD"),
            target_feasible=True,
            commitments=[
                {
                    "po_number": po.po_number,
                    "supplier_id": po.supplier_id,
                    "total": str(po.total),
                }
                for po in purchase_orders
            ],
            audit_references=list(snapshot.state.get("comparisons", {}).keys()),
        )
        await self._send_chat(
            ctx,
            snapshot.conversation_id,
            "Procurement is complete and the supplier commitments are confirmed.",
            card.metadata(),
        )
        if self.audit is not None:
            activities = self.audit.list_activity(snapshot.workflow_id)
            await self._send_chat(
                ctx,
                snapshot.conversation_id,
                "Agent activity and durable workflow evidence are available for review.",
                agent_activity_detail(activities).metadata(),
            )

    def _transition_reservations(
        self, workflow_id: UUID, status: Literal["COMMITTED", "RELEASED"]
    ) -> None:
        if self.reservation_store is None:
            return
        changed = self.reservation_store.transition_reservations(workflow_id, status=status)
        if self.audit is not None:
            self.audit.append_event(
                workflow_id=workflow_id,
                actor_type="AGENT",
                actor_id="manufacturing-orchestrator",
                action=f"RESERVATIONS_{status}",
                entity_type="inventory_reservation",
                entity_id=str(workflow_id),
                before={"status": "TENTATIVE"},
                after={"status": status, "changed": changed},
            )

    async def _fail(
        self,
        ctx: MessageContext,
        snapshot: WorkflowSnapshot,
        code: str,
        message: str,
        inbound_message_id: UUID,
    ) -> None:
        accepted = self.routing_store.update(
            snapshot.workflow_id,
            status="FAILED_REQUIRES_OPERATOR",
            updates={"error": {"code": code, "message": message, "retryable": False}},
            inbound_message_id=inbound_message_id,
        )
        if accepted:
            await self._send_chat(
                ctx,
                snapshot.conversation_id,
                f"The workflow needs operator review: {message}",
            )

    async def _send_specialist(
        self,
        ctx: MessageContext,
        snapshot: WorkflowSnapshot,
        destination: str,
        wire: object,
        message_id: UUID,
        message_type: str,
    ) -> None:
        sent_at = datetime.now(UTC)
        payload_json = getattr(wire, "payload_json", repr(wire))
        payload_hash = sha256(str(payload_json).encode()).hexdigest()
        self.routing_store.record_outbound(
            snapshot.workflow_id,
            message_id=message_id,
            destination=destination,
            message_type=message_type,
            status="PENDING",
            payload_json=str(payload_json),
        )
        if self.audit is not None:
            self.audit.log_message(
                workflow_id=snapshot.workflow_id,
                message_id=message_id,
                sender="manufacturing-orchestrator",
                recipient=destination,
                message_type=message_type,
                payload_hash=payload_hash,
                status="PENDING",
                sent_at=sent_at,
            )
        try:
            delivery = await ctx.send(destination, wire)
            failed = getattr(delivery, "status", None) == DeliveryStatus.FAILED
        except Exception:
            failed = True
        self.routing_store.record_outbound(
            snapshot.workflow_id,
            message_id=message_id,
            destination=destination,
            message_type=message_type,
            status="FAILED" if failed else "DELIVERED",
        )
        if self.audit is not None:
            self.audit.log_message(
                workflow_id=snapshot.workflow_id,
                message_id=message_id,
                sender="manufacturing-orchestrator",
                recipient=destination,
                message_type=message_type,
                payload_hash=payload_hash,
                status="FAILED" if failed else "DELIVERED",
                sent_at=sent_at,
                received_at=datetime.now(UTC),
            )
        if failed:
            await self._fail(
                ctx,
                self._snapshot(snapshot.workflow_id),
                "SPECIALIST_UNREACHABLE",
                f"The {message_type} destination could not be reached safely.",
                message_id,
            )

    async def recover_outbound(self, ctx: MessageContext) -> None:
        for pending in self.routing_store.pending_outbound():
            wire = self._outbound_wire(pending)
            delivery = await ctx.send(pending.destination, wire)
            failed = getattr(delivery, "status", None) == DeliveryStatus.FAILED
            self.routing_store.record_outbound(
                pending.workflow_id,
                message_id=pending.message_id,
                destination=pending.destination,
                message_type=pending.message_type,
                status="FAILED" if failed else "DELIVERED",
            )
            if self.audit is not None:
                self.audit.log_message(
                    workflow_id=pending.workflow_id,
                    message_id=pending.message_id,
                    sender="manufacturing-orchestrator",
                    recipient=pending.destination,
                    message_type=pending.message_type,
                    payload_hash=sha256(pending.payload_json.encode()).hexdigest(),
                    status="FAILED" if failed else "DELIVERED",
                    sent_at=datetime.now(UTC),
                    received_at=datetime.now(UTC),
                )

    @staticmethod
    def _outbound_wire(pending: PendingOutbound) -> object:
        builders: dict[str, Any] = {
            "DemandPlanningRequest": lambda: DemandPlanningTransport.from_domain(
                DemandPlanningRequest.model_validate_json(pending.payload_json)
            ),
            "SourcingRequest": lambda: SourcingRequestTransport.from_domain(
                SourcingRequest.model_validate_json(pending.payload_json)
            ),
            "PurchaseOrderExecutionRequest": lambda: PurchaseOrderExecutionTransport.from_domain(
                PurchaseOrderExecutionRequest.model_validate_json(pending.payload_json)
            ),
            "CounterproposalExecutionRequest": lambda: (
                CounterproposalExecutionTransport.from_domain(
                    CounterproposalExecutionRequest.model_validate_json(pending.payload_json)
                )
            ),
            "AcknowledgmentReceived": lambda: AcknowledgmentReceivedTransport.from_domain(
                AcknowledgmentReceived.model_validate_json(pending.payload_json)
            ),
            "ChangeResponseReceived": lambda: ChangeResponseReceivedTransport.from_domain(
                ChangeResponseReceived.model_validate_json(pending.payload_json)
            ),
        }
        builder = builders.get(pending.message_type)
        if builder is None:
            raise ValueError(f"unsupported pending outbound type: {pending.message_type}")
        return builder()

    @staticmethod
    async def _send_chat(
        ctx: MessageContext,
        conversation_id: str,
        text: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        content: list[TextContent | MetadataContent] = [TextContent(text=text)]
        if metadata is not None:
            content.append(MetadataContent(metadata=metadata))
        await ctx.send(conversation_id, ChatMessage(timestamp=datetime.now(UTC), content=content))

    def _snapshot(self, workflow_id: UUID) -> WorkflowSnapshot:
        snapshot = self.routing_store.snapshot(workflow_id)
        if snapshot is None:
            raise ValueError("message references an unknown workflow")
        return snapshot

    def _audit_received(self, message: BaseModel) -> None:
        if self.audit is None:
            return
        message_id = getattr(message, "message_id", None)
        workflow_id = getattr(message, "workflow_id", None)
        sender = getattr(message, "sender_agent", None)
        if not isinstance(message_id, UUID) or not isinstance(workflow_id, UUID) or not sender:
            return
        observed_at = datetime.now(UTC)
        self.audit.log_message(
            workflow_id=workflow_id,
            message_id=message_id,
            sender=str(sender),
            recipient="manufacturing-orchestrator",
            message_type=str(getattr(message, "message_type", type(message).__name__)),
            payload_hash=sha256(message.model_dump_json().encode()).hexdigest(),
            status="RECEIVED",
            sent_at=getattr(message, "sent_at", observed_at),
            received_at=observed_at,
        )

    def _validated_reply(
        self,
        workflow_id: UUID,
        correlation_id: UUID,
        reply_to: str | None,
        request_key: str,
    ) -> WorkflowSnapshot:
        snapshot = self._snapshot(workflow_id)
        self._validate_correlation(snapshot, correlation_id)
        request = snapshot.state.get(request_key)
        if not isinstance(request, dict) or reply_to != str(request.get("message_id")):
            raise ValueError("specialist response does not answer the pending request")
        return snapshot

    @staticmethod
    def _validate_correlation(snapshot: WorkflowSnapshot, correlation_id: UUID) -> None:
        if snapshot.state.get("correlation_id") != str(correlation_id):
            raise ValueError("message correlation ID does not match the workflow")

    @staticmethod
    def _already_processed(snapshot: WorkflowSnapshot, message_id: UUID) -> bool:
        return str(message_id) in snapshot.state.get("processed_message_ids", [])

    @staticmethod
    def _require_status(snapshot: WorkflowSnapshot, expected: str) -> None:
        if snapshot.status != expected:
            raise ValueError(f"workflow is {snapshot.status}, expected {expected}")

    @staticmethod
    def _plan(snapshot: WorkflowSnapshot, plan_id: str | None) -> ProcurementPlan:
        for value in snapshot.state.get("plans", []):
            plan = ProcurementPlan.model_validate(value)
            if plan.plan_id == plan_id:
                return plan
        raise ValueError("selected procurement plan was not found")

    @staticmethod
    def _purchase_order(snapshot: WorkflowSnapshot, po_id: str | None) -> PurchaseOrder:
        for value in snapshot.state.get("purchase_orders", []):
            po = PurchaseOrder.model_validate(value)
            if str(po.po_id) == po_id:
                return po
        raise ValueError("purchase order was not found in the workflow")

    def _advance(self, snapshot: WorkflowSnapshot, event_kind: str) -> str:
        if self.transition_graph is None:
            raise RuntimeError("orchestrator transition graph is not configured")
        state = cast(
            ProcurementWorkflowState,
            {**snapshot.state, "status": snapshot.status, "event": {"kind": event_kind}},
        )
        result = self.transition_graph.invoke(state)
        return str(result["status"])


def build_specialist_response_protocol(
    coordinator: OrchestrationCoordinator, *, recovery_interval_seconds: float = 2
) -> AgentProtocol:
    protocol = AgentProtocol(name="SupplyMeshOrchestratorSpecialists", version="1.0.0")

    @protocol.on_message(SupplierQuotesCollectedTransport)
    async def quotes(ctx: Context, sender: str, msg: SupplierQuotesCollectedTransport) -> None:
        del sender
        await _bounded(ctx, coordinator.handle_quotes, msg)

    @protocol.on_message(ProcurementPlansReadyTransport)
    async def plans(ctx: Context, sender: str, msg: ProcurementPlansReadyTransport) -> None:
        del sender
        await _bounded(ctx, coordinator.handle_plans, msg)

    @protocol.on_message(PurchaseOrderSubmittedTransport)
    async def submitted(ctx: Context, sender: str, msg: PurchaseOrderSubmittedTransport) -> None:
        del sender
        await _bounded(ctx, coordinator.handle_po_submitted, msg)

    @protocol.on_message(AcknowledgmentReceivedTransport)
    async def acknowledgment(
        ctx: Context, sender: str, msg: AcknowledgmentReceivedTransport
    ) -> None:
        del sender
        await _bounded(ctx, coordinator.handle_acknowledgment, msg)

    @protocol.on_message(AcknowledgmentComparisonTransport)
    async def comparison(ctx: Context, sender: str, msg: AcknowledgmentComparisonTransport) -> None:
        del sender
        await _bounded(ctx, coordinator.handle_comparison, msg)

    @protocol.on_message(ChangeOrderSubmittedTransport)
    async def change_submitted(
        ctx: Context, sender: str, msg: ChangeOrderSubmittedTransport
    ) -> None:
        del sender
        await _bounded(ctx, coordinator.handle_change_submitted, msg)

    @protocol.on_message(ChangeResponseReceivedTransport)
    async def change_response(
        ctx: Context, sender: str, msg: ChangeResponseReceivedTransport
    ) -> None:
        del sender
        await _bounded(ctx, coordinator.handle_change_response, msg)

    @protocol.on_message(WorkflowStatusResponseTransport)
    async def workflow_status(
        ctx: Context, sender: str, msg: WorkflowStatusResponseTransport
    ) -> None:
        del sender
        await _bounded(ctx, coordinator.handle_change_status, msg)

    @protocol.on_message(SpecialistFailureTransport)
    async def failure(ctx: Context, sender: str, msg: SpecialistFailureTransport) -> None:
        del sender
        try:
            await coordinator.handle_failure(ctx, msg)
        except (ValidationError, ValueError):
            ctx.logger.warning("rejected invalid or stale specialist failure")

    @protocol.on_interval(
        recovery_interval_seconds,
        messages={
            DemandPlanningTransport,
            SourcingRequestTransport,
            PurchaseOrderExecutionTransport,
            CounterproposalExecutionTransport,
            AcknowledgmentReceivedTransport,
            ChangeResponseReceivedTransport,
        },
    )
    async def recover_pending_outbound(ctx: Context) -> None:
        try:
            await coordinator.recover_outbound(ctx)
        except Exception:
            ctx.logger.exception("orchestrator outbound recovery failed")

    return protocol


async def _bounded(ctx: Context, handler: Any, msg: Any) -> None:
    try:
        await handler(ctx, msg.to_domain())
    except (ValidationError, ValueError):
        ctx.logger.warning("rejected invalid or stale specialist response")
    except Exception:
        ctx.logger.exception("orchestrator specialist response failed")
