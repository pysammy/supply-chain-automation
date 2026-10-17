import asyncio
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID, uuid4, uuid5

from pydantic import ValidationError
from uagents import Context
from uagents import Protocol as AgentProtocol

from supplymesh.agents.transport import (
    AcknowledgmentReceivedTransport,
    ChangeOrderSubmittedTransport,
    ChangeResponseReceivedTransport,
    CounterproposalExecutionTransport,
    PurchaseOrderExecutionTransport,
    PurchaseOrderSubmittedTransport,
    SpecialistFailureTransport,
)
from supplymesh.domain.procurement import (
    ApprovalRecord,
    ChangeTransactionStatus,
    Counterproposal,
    PurchaseOrder,
    SupplierTransactionAccepted,
    SupplierTransactionStatus,
)
from supplymesh.edi.x12_850 import generate_850
from supplymesh.edi.x12_860 import ChangeRequestLine, generate_860
from supplymesh.persistence.procurement_repository import PendingSupplierTransaction
from supplymesh.protocols.procurement import (
    AcknowledgmentReceived,
    ChangeOrderSubmitted,
    ChangeResponseReceived,
    CounterproposalExecutionRequest,
    PurchaseOrderExecutionRequest,
    PurchaseOrderSubmitted,
)
from supplymesh.services.counterproposal_service import (
    ChangeOrderGateway,
    counterproposal_idempotency_key,
    execute_counterproposal,
)
from supplymesh.services.procurement_service import (
    PurchaseOrderGateway,
    SideEffectJournal,
    control_number,
    execute_purchase_order,
    purchase_order_idempotency_key,
)


class ProcurementGateway(PurchaseOrderGateway, ChangeOrderGateway, Protocol):
    async def get_purchase_order_status(
        self, supplier_id: str, transaction_id: str, *, correlation_id: str
    ) -> SupplierTransactionStatus: ...

    async def get_change_order_status(
        self, supplier_id: str, transaction_id: str, *, correlation_id: str
    ) -> ChangeTransactionStatus: ...


class ProcurementState(Protocol):
    def get_approval(self, approval_id: str) -> ApprovalRecord: ...

    def save_approved_purchase_order(self, po: PurchaseOrder) -> None: ...

    def record_submission(
        self,
        po: PurchaseOrder,
        *,
        external_transaction_id: str,
        poll_url: str,
    ) -> None: ...

    def record_purchase_order_intent(
        self,
        po: PurchaseOrder,
        *,
        correlation_id: UUID,
        request_message_id: UUID,
        response_message_id: UUID,
        approval_id: UUID,
        reply_address: str,
    ) -> None: ...

    def record_change_order_intent(
        self,
        counterproposal: Counterproposal,
        *,
        correlation_id: UUID,
        request_message_id: UUID,
        response_message_id: UUID,
        approval_id: UUID,
        reply_address: str,
    ) -> None: ...

    def record_change_submission(
        self,
        counterproposal: Counterproposal,
        *,
        external_transaction_id: str,
        poll_url: str,
    ) -> None: ...

    def mark_transaction_ready(self, supplier_id: str, idempotency_key: str) -> None: ...

    def pending_transactions(self) -> tuple[PendingSupplierTransaction, ...]: ...

    def claim_pending_transactions(
        self, owner: str, *, lease_seconds: float = 30
    ) -> tuple[PendingSupplierTransaction, ...]: ...

    def release_claim(self, response_message_id: UUID, owner: str) -> None: ...

    def mark_response_delivered(self, response_message_id: UUID) -> None: ...

    def record_edi_document(
        self,
        *,
        workflow_id: UUID,
        supplier_id: str,
        document_type: Literal["850", "855", "860", "865"],
        direction: Literal["OUTBOUND", "INBOUND"],
        raw_text: str,
        related_document_id: UUID,
    ) -> UUID: ...

    def record_change_order(
        self, counterproposal: Counterproposal, *, approval_id: UUID
    ) -> UUID: ...


@dataclass(frozen=True)
class RecoveryDelivery:
    destination: str
    submitted: PurchaseOrderSubmitted | ChangeOrderSubmitted
    response: AcknowledgmentReceived | ChangeResponseReceived


class ProcurementAgentService:
    name = "procurement-execution"

    def __init__(
        self,
        gateway: ProcurementGateway,
        journal: SideEffectJournal,
        state: ProcurementState,
        *,
        poll_interval_seconds: float = 2,
        poll_timeout_seconds: float = 45,
    ) -> None:
        self.gateway = gateway
        self.journal = journal
        self.state = state
        self.poll_interval_seconds = poll_interval_seconds
        self.poll_timeout_seconds = poll_timeout_seconds
        self.recovery_owner = f"procurement-{uuid4()}"

    async def execute_purchase_orders(
        self, request: PurchaseOrderExecutionRequest, *, reply_address: str
    ) -> tuple[tuple[PurchaseOrderSubmitted, AcknowledgmentReceived], ...]:
        approval_ids = request.approval_ids or (request.approval_id,)
        if len(approval_ids) != len(request.purchase_orders):
            raise ValueError("each purchase order requires its own approval record")
        results: list[tuple[PurchaseOrderSubmitted, AcknowledgmentReceived]] = []
        for po, approval_id in zip(request.purchase_orders, approval_ids, strict=True):
            approval = self.state.get_approval(approval_id)
            self.state.save_approved_purchase_order(po)
            key = purchase_order_idempotency_key(po)
            self.state.record_edi_document(
                workflow_id=po.workflow_id,
                supplier_id=po.supplier_id,
                document_type="850",
                direction="OUTBOUND",
                raw_text=generate_850(po, control_number(key)),
                related_document_id=po.po_id,
            )
            response_message_id = uuid5(
                request.message_id,
                f"purchase-order-submitted:{po.po_id}",
            )
            self.state.record_purchase_order_intent(
                po,
                correlation_id=request.correlation_id,
                request_message_id=request.message_id,
                response_message_id=response_message_id,
                approval_id=approval.approval_id,
                reply_address=reply_address,
            )
            accepted = await execute_purchase_order(
                po,
                approval,
                self.gateway,
                self.journal,
                correlation_id=str(request.correlation_id),
                submission_message_id=uuid5(response_message_id, "supplier-submission"),
            )
            self.state.record_submission(
                po,
                external_transaction_id=accepted.supplier_transaction_id,
                poll_url=accepted.poll_url,
            )
            submitted = PurchaseOrderSubmitted(
                message_id=response_message_id,
                correlation_id=request.correlation_id,
                workflow_id=request.workflow_id,
                sender_agent=self.name,
                reply_to=str(request.message_id),
                po_id=str(po.po_id),
                supplier_transaction_id=accepted.supplier_transaction_id,
                poll_url=accepted.poll_url,
            )
            status = await self._poll_purchase_order(po, accepted, request)
            self.state.mark_transaction_ready(po.supplier_id, key)
            results.append(
                (
                    submitted,
                    AcknowledgmentReceived(
                        message_id=uuid5(response_message_id, "acknowledgment"),
                        correlation_id=request.correlation_id,
                        workflow_id=request.workflow_id,
                        sender_agent=self.name,
                        reply_to=str(submitted.message_id),
                        po_id=str(po.po_id),
                        po_version=po.version,
                        raw_x12=status.x12 or "",
                    ),
                )
            )
        return tuple(results)

    async def execute_counterproposal(
        self, request: CounterproposalExecutionRequest, *, reply_address: str
    ) -> tuple[ChangeOrderSubmitted, ChangeResponseReceived]:
        approval = self.state.get_approval(request.approval_id)
        self.state.record_change_order(
            request.counterproposal,
            approval_id=approval.approval_id,
        )
        counter = request.counterproposal
        key = counterproposal_idempotency_key(counter)
        self.state.record_edi_document(
            workflow_id=counter.workflow_id,
            supplier_id=counter.supplier_id,
            document_type="860",
            direction="OUTBOUND",
            raw_text=generate_860(
                po_number=counter.po_number,
                po_version=counter.change_version,
                currency=counter.currency,
                lines=tuple(
                    ChangeRequestLine(
                        line_number=line.line_number,
                        component_sku=line.component_sku,
                        quantity=line.quantity,
                        unit=line.unit,
                        maximum_unit_price=line.maximum_unit_price,
                        requested_date=line.requested_date,
                    )
                    for line in counter.lines
                ),
                control_number=control_number(key),
            ),
            related_document_id=counter.po_id,
        )
        response_message_id = uuid5(
            request.message_id,
            f"change-order-submitted:{counter.po_id}:{counter.change_version}",
        )
        self.state.record_change_order_intent(
            counter,
            correlation_id=request.correlation_id,
            request_message_id=request.message_id,
            response_message_id=response_message_id,
            approval_id=approval.approval_id,
            reply_address=reply_address,
        )
        accepted = await execute_counterproposal(
            request.counterproposal,
            approval,
            self.gateway,
            self.journal,
            correlation_id=str(request.correlation_id),
            submission_message_id=uuid5(response_message_id, "supplier-submission"),
        )
        self.state.record_change_submission(
            counter,
            external_transaction_id=accepted.supplier_transaction_id,
            poll_url=accepted.poll_url,
        )
        submitted = ChangeOrderSubmitted(
            message_id=response_message_id,
            correlation_id=request.correlation_id,
            workflow_id=request.workflow_id,
            sender_agent=self.name,
            reply_to=str(request.message_id),
            po_id=str(request.counterproposal.po_id),
            change_version=request.counterproposal.change_version,
            supplier_transaction_id=accepted.supplier_transaction_id,
        )
        status = await self._poll_change_order(request, accepted)
        self.state.mark_transaction_ready(counter.supplier_id, key)
        return submitted, ChangeResponseReceived(
            message_id=uuid5(response_message_id, "acknowledgment"),
            correlation_id=request.correlation_id,
            workflow_id=request.workflow_id,
            sender_agent=self.name,
            reply_to=str(submitted.message_id),
            po_id=str(request.counterproposal.po_id),
            po_version=request.counterproposal.change_version,
            raw_x12=status.x12 or "",
        )

    async def recover_pending(self) -> tuple[RecoveryDelivery, ...]:
        deliveries: list[RecoveryDelivery] = []
        for pending in self.state.claim_pending_transactions(self.recovery_owner):
            if pending.transaction_type == "PURCHASE_ORDER":
                delivery = await self._recover_purchase_order(pending)
            else:
                delivery = await self._recover_change_order(pending)
            if delivery is not None:
                deliveries.append(delivery)
        return tuple(deliveries)

    async def _recover_purchase_order(
        self, pending: PendingSupplierTransaction
    ) -> RecoveryDelivery | None:
        po = pending.purchase_order
        if po is None:
            raise ValueError("purchase-order recovery record has no purchase order")
        external_transaction_id = pending.external_transaction_id
        poll_url = pending.poll_url
        if external_transaction_id is None or poll_url is None:
            approval = self.state.get_approval(str(pending.approval_id))
            accepted = await execute_purchase_order(
                po,
                approval,
                self.gateway,
                self.journal,
                correlation_id=str(pending.correlation_id),
                submission_message_id=uuid5(pending.response_message_id, "supplier-submission"),
            )
            external_transaction_id = accepted.supplier_transaction_id
            poll_url = accepted.poll_url
            self.state.record_submission(
                po,
                external_transaction_id=external_transaction_id,
                poll_url=poll_url,
            )
        response_x12 = pending.response_x12
        if pending.status != "READY_TO_DELIVER":
            status = await self.gateway.get_purchase_order_status(
                po.supplier_id,
                external_transaction_id,
                correlation_id=str(pending.correlation_id),
            )
            if status.status != "ACKNOWLEDGED" or status.document_type != "855" or not status.x12:
                self.state.release_claim(pending.response_message_id, self.recovery_owner)
                return None
            response_x12 = status.x12
            self.state.record_edi_document(
                workflow_id=po.workflow_id,
                supplier_id=po.supplier_id,
                document_type="855",
                direction="INBOUND",
                raw_text=response_x12,
                related_document_id=po.po_id,
            )
            self.state.mark_transaction_ready(po.supplier_id, purchase_order_idempotency_key(po))
        if response_x12 is None:
            raise ValueError("ready purchase-order delivery has no persisted 855")
        submitted = PurchaseOrderSubmitted(
            message_id=pending.response_message_id,
            correlation_id=pending.correlation_id,
            workflow_id=pending.workflow_id,
            sender_agent=self.name,
            reply_to=str(pending.request_message_id),
            po_id=str(po.po_id),
            supplier_transaction_id=external_transaction_id,
            poll_url=poll_url,
        )
        return RecoveryDelivery(
            destination=pending.reply_address,
            submitted=submitted,
            response=AcknowledgmentReceived(
                message_id=uuid5(pending.response_message_id, "acknowledgment"),
                correlation_id=pending.correlation_id,
                workflow_id=pending.workflow_id,
                sender_agent=self.name,
                reply_to=str(pending.response_message_id),
                po_id=str(po.po_id),
                po_version=po.version,
                raw_x12=response_x12,
            ),
        )

    async def _recover_change_order(
        self, pending: PendingSupplierTransaction
    ) -> RecoveryDelivery | None:
        counter = pending.counterproposal
        if counter is None:
            raise ValueError("change-order recovery record has no counterproposal")
        external_transaction_id = pending.external_transaction_id
        poll_url = pending.poll_url
        if external_transaction_id is None or poll_url is None:
            approval = self.state.get_approval(str(pending.approval_id))
            accepted = await execute_counterproposal(
                counter,
                approval,
                self.gateway,
                self.journal,
                correlation_id=str(pending.correlation_id),
                submission_message_id=uuid5(pending.response_message_id, "supplier-submission"),
            )
            external_transaction_id = accepted.supplier_transaction_id
            poll_url = accepted.poll_url
            self.state.record_change_submission(
                counter,
                external_transaction_id=external_transaction_id,
                poll_url=poll_url,
            )
        response_x12 = pending.response_x12
        if pending.status != "READY_TO_DELIVER":
            status = await self.gateway.get_change_order_status(
                counter.supplier_id,
                external_transaction_id,
                correlation_id=str(pending.correlation_id),
            )
            if status.status != "ACKNOWLEDGED" or status.document_type != "865" or not status.x12:
                self.state.release_claim(pending.response_message_id, self.recovery_owner)
                return None
            response_x12 = status.x12
            self.state.record_edi_document(
                workflow_id=counter.workflow_id,
                supplier_id=counter.supplier_id,
                document_type="865",
                direction="INBOUND",
                raw_text=response_x12,
                related_document_id=counter.po_id,
            )
            self.state.mark_transaction_ready(
                counter.supplier_id, counterproposal_idempotency_key(counter)
            )
        if response_x12 is None:
            raise ValueError("ready change-order delivery has no persisted 865")
        submitted = ChangeOrderSubmitted(
            message_id=pending.response_message_id,
            correlation_id=pending.correlation_id,
            workflow_id=pending.workflow_id,
            sender_agent=self.name,
            reply_to=str(pending.request_message_id),
            po_id=str(counter.po_id),
            change_version=counter.change_version,
            supplier_transaction_id=external_transaction_id,
        )
        return RecoveryDelivery(
            destination=pending.reply_address,
            submitted=submitted,
            response=ChangeResponseReceived(
                message_id=uuid5(pending.response_message_id, "acknowledgment"),
                correlation_id=pending.correlation_id,
                workflow_id=pending.workflow_id,
                sender_agent=self.name,
                reply_to=str(pending.response_message_id),
                po_id=str(counter.po_id),
                po_version=counter.change_version,
                raw_x12=response_x12,
            ),
        )

    def mark_delivery_complete(
        self, submitted: PurchaseOrderSubmitted | ChangeOrderSubmitted
    ) -> None:
        self.state.mark_response_delivered(submitted.message_id)

    async def _poll_purchase_order(
        self,
        po: PurchaseOrder,
        accepted: SupplierTransactionAccepted,
        request: PurchaseOrderExecutionRequest,
    ) -> SupplierTransactionStatus:
        attempts = self._attempt_limit()
        for attempt in range(attempts):
            status = await self.gateway.get_purchase_order_status(
                po.supplier_id,
                accepted.supplier_transaction_id,
                correlation_id=str(request.correlation_id),
            )
            if status.status == "ACKNOWLEDGED" and status.document_type == "855" and status.x12:
                self.state.record_edi_document(
                    workflow_id=po.workflow_id,
                    supplier_id=po.supplier_id,
                    document_type="855",
                    direction="INBOUND",
                    raw_text=status.x12,
                    related_document_id=po.po_id,
                )
                return status
            if attempt + 1 < attempts:
                await asyncio.sleep(status.retry_after_seconds or self.poll_interval_seconds)
        raise RuntimeError("supplier acknowledgment polling timed out")

    async def _poll_change_order(
        self,
        request: CounterproposalExecutionRequest,
        accepted: SupplierTransactionAccepted,
    ) -> ChangeTransactionStatus:
        attempts = self._attempt_limit()
        for attempt in range(attempts):
            status = await self.gateway.get_change_order_status(
                request.counterproposal.supplier_id,
                accepted.supplier_transaction_id,
                correlation_id=str(request.correlation_id),
            )
            if status.status == "ACKNOWLEDGED" and status.document_type == "865" and status.x12:
                counter = request.counterproposal
                self.state.record_edi_document(
                    workflow_id=counter.workflow_id,
                    supplier_id=counter.supplier_id,
                    document_type="865",
                    direction="INBOUND",
                    raw_text=status.x12,
                    related_document_id=counter.po_id,
                )
                return status
            if attempt + 1 < attempts:
                await asyncio.sleep(status.retry_after_seconds or self.poll_interval_seconds)
        raise RuntimeError("supplier change-response polling timed out")

    def _attempt_limit(self) -> int:
        return max(1, int(self.poll_timeout_seconds / self.poll_interval_seconds) + 1)


def build_procurement_protocol(
    service: ProcurementAgentService, *, recovery_interval_seconds: float = 2
) -> AgentProtocol:
    protocol = AgentProtocol(name="SupplyMeshProcurementExecution", version="1.0.0")

    @protocol.on_message(
        PurchaseOrderExecutionTransport,
        replies={
            PurchaseOrderSubmittedTransport,
            AcknowledgmentReceivedTransport,
            SpecialistFailureTransport,
        },
    )
    async def handle_purchase_orders(
        ctx: Context, sender: str, msg: PurchaseOrderExecutionTransport
    ) -> None:
        try:
            request = msg.to_domain()
            for submitted, acknowledgment in await service.execute_purchase_orders(
                request, reply_address=sender
            ):
                await ctx.send(sender, PurchaseOrderSubmittedTransport.from_domain(submitted))
                await ctx.send(sender, AcknowledgmentReceivedTransport.from_domain(acknowledgment))
                service.mark_delivery_complete(submitted)
        except (ValidationError, ValueError, RuntimeError):
            await _send_failure(ctx, sender, msg, service.name, "PURCHASE_ORDER_EXECUTION")
        except Exception:
            ctx.logger.exception("purchase-order transport execution failed")
            await _send_failure(ctx, sender, msg, service.name, "PURCHASE_ORDER_EXECUTION")

    @protocol.on_message(
        CounterproposalExecutionTransport,
        replies={
            ChangeOrderSubmittedTransport,
            ChangeResponseReceivedTransport,
            SpecialistFailureTransport,
        },
    )
    async def handle_counterproposal(
        ctx: Context, sender: str, msg: CounterproposalExecutionTransport
    ) -> None:
        try:
            request = msg.to_domain()
            submitted, response = await service.execute_counterproposal(
                request, reply_address=sender
            )
            await ctx.send(sender, ChangeOrderSubmittedTransport.from_domain(submitted))
            await ctx.send(sender, ChangeResponseReceivedTransport.from_domain(response))
            service.mark_delivery_complete(submitted)
        except (ValidationError, ValueError, RuntimeError):
            await _send_failure(ctx, sender, msg, service.name, "COUNTERPROPOSAL_EXECUTION")
        except Exception:
            ctx.logger.exception("counterproposal transport execution failed")
            await _send_failure(ctx, sender, msg, service.name, "COUNTERPROPOSAL_EXECUTION")

    @protocol.on_interval(
        recovery_interval_seconds,
        messages={
            PurchaseOrderSubmittedTransport,
            AcknowledgmentReceivedTransport,
            ChangeOrderSubmittedTransport,
            ChangeResponseReceivedTransport,
        },
    )
    async def recover_incomplete_supplier_transactions(ctx: Context) -> None:
        try:
            for delivery in await service.recover_pending():
                if isinstance(delivery.submitted, PurchaseOrderSubmitted):
                    await ctx.send(
                        delivery.destination,
                        PurchaseOrderSubmittedTransport.from_domain(delivery.submitted),
                    )
                    assert isinstance(delivery.response, AcknowledgmentReceived)
                    await ctx.send(
                        delivery.destination,
                        AcknowledgmentReceivedTransport.from_domain(delivery.response),
                    )
                else:
                    await ctx.send(
                        delivery.destination,
                        ChangeOrderSubmittedTransport.from_domain(delivery.submitted),
                    )
                    assert isinstance(delivery.response, ChangeResponseReceived)
                    await ctx.send(
                        delivery.destination,
                        ChangeResponseReceivedTransport.from_domain(delivery.response),
                    )
                service.mark_delivery_complete(delivery.submitted)
        except Exception:
            ctx.logger.exception("supplier transaction recovery failed")

    return protocol


async def _send_failure(
    ctx: Context,
    sender: str,
    msg: PurchaseOrderExecutionTransport | CounterproposalExecutionTransport,
    agent_name: str,
    operation: str,
) -> None:
    await ctx.send(
        sender,
        SpecialistFailureTransport(
            correlation_id=msg.correlation_id,
            workflow_id=msg.workflow_id,
            reply_to=msg.message_id,
            sender_agent=agent_name,
            operation=operation,
            code="PROCUREMENT_EXECUTION_FAILED",
            message="Approved procurement execution could not be completed safely.",
            retryable=True,
        ),
    )
