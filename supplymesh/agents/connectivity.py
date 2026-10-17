from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from uagents_core.contrib.protocols.chat import ChatMessage, MetadataContent, TextContent
from uagents_core.types import DeliveryStatus

from supplymesh.agents.transport import DemandPlanningTransport, PlanningFailureTransport
from supplymesh.cards.demand import DemandIntake
from supplymesh.cards.inventory import inventory_assessment_detail
from supplymesh.persistence.workflow_repository import ConnectivitySession
from supplymesh.protocols.planning import DemandPlanningRequest, InventoryShortageReport


class MessageContext(Protocol):
    async def send(self, destination: str, message: object, timeout: int = 30) -> object: ...


class PlanningRequestFactory(Protocol):
    def create_request(
        self,
        intake: DemandIntake,
        *,
        workflow_id: UUID,
        correlation_id: UUID,
    ) -> DemandPlanningRequest: ...


class ConnectivityStore(Protocol):
    def start_inventory(self, session_state: ConnectivitySession) -> None: ...

    def get(self, workflow_id: UUID) -> ConnectivitySession | None: ...

    def find_by_chat_message(self, chat_message_id: UUID) -> ConnectivitySession | None: ...

    def record_inventory_result(self, workflow_id: UUID, result: dict[str, object]) -> None: ...

    def record_failure(self, workflow_id: UUID, error: dict[str, object]) -> None: ...


@dataclass(frozen=True)
class FetchConnectivityCoordinator:
    inventory_address: str
    requests: PlanningRequestFactory
    workflows: ConnectivityStore

    async def start_inventory(
        self,
        ctx: MessageContext,
        *,
        conversation_id: str,
        chat_message_id: UUID,
        intake: DemandIntake,
    ) -> DemandPlanningRequest | None:
        existing = self.workflows.find_by_chat_message(chat_message_id)
        if existing is not None:
            await ctx.send(
                existing.conversation_id,
                ChatMessage(
                    timestamp=datetime.now(UTC),
                    content=[
                        TextContent(
                            text=(
                                "This demand submission was already accepted and inventory "
                                f"planning is {existing.status.lower().replace('_', ' ')}."
                            )
                        )
                    ],
                ),
            )
            return None
        workflow_id = uuid4()
        correlation_id = uuid4()
        request = self.requests.create_request(
            intake,
            workflow_id=workflow_id,
            correlation_id=correlation_id,
        )
        self.workflows.start_inventory(
            ConnectivitySession(
                workflow_id=workflow_id,
                conversation_id=conversation_id,
                correlation_id=correlation_id,
                request_message_id=request.message_id,
                chat_message_id=chat_message_id,
                status="INVENTORY_PLANNING",
                currency=intake.currency,
            )
        )
        self._record_inventory_delivery(request, "PENDING")
        delivery = await ctx.send(
            self.inventory_address, DemandPlanningTransport.from_domain(request)
        )
        if getattr(delivery, "status", None) == DeliveryStatus.FAILED:
            self._record_inventory_delivery(request, "FAILED")
            error = {
                "code": "INVENTORY_AGENT_UNREACHABLE",
                "message": "The Inventory Agent could not be reached.",
                "retryable": True,
            }
            self.workflows.record_failure(workflow_id, error)
            raise ConnectivityError(error["message"])
        self._record_inventory_delivery(request, "DELIVERED")
        return request

    async def complete_inventory(
        self, ctx: MessageContext, report: InventoryShortageReport
    ) -> None:
        session = self._validated_session(report)
        assessment = report.assessment
        self.workflows.record_inventory_result(
            report.workflow_id,
            {
                "assessment": assessment.model_dump(mode="json"),
                "has_shortages": assessment.has_shortages,
                "report_message_id": str(report.message_id),
            },
        )
        card = inventory_assessment_detail(assessment)
        shortage_count = sum(1 for line in assessment.lines if line.shortage > 0)
        await ctx.send(
            session.conversation_id,
            ChatMessage(
                timestamp=datetime.now(UTC),
                content=[
                    TextContent(
                        text=(
                            "Inventory planning is complete. Existing reservations and safety "
                            f"stock remain protected; {shortage_count} component line(s) require "
                            "sourcing."
                        )
                    ),
                    MetadataContent(metadata=card.metadata()),
                ],
            ),
        )

    async def fail_inventory(self, ctx: MessageContext, failure: PlanningFailureTransport) -> None:
        workflow_id = UUID(failure.workflow_id)
        session = self.workflows.get(workflow_id)
        if session is None:
            raise ValueError("planning failure references an unknown workflow")
        if (
            str(session.correlation_id) != failure.correlation_id
            or str(session.request_message_id) != failure.reply_to
        ):
            raise ValueError("planning failure does not match the pending request")
        error = {
            "code": failure.code,
            "message": failure.message,
            "retryable": failure.retryable,
        }
        self.workflows.record_failure(workflow_id, error)
        await ctx.send(
            session.conversation_id,
            ChatMessage(
                timestamp=datetime.now(UTC),
                content=[
                    TextContent(
                        text=(
                            "Inventory planning could not be completed. "
                            f"{failure.message} Please correct the demand or ask an operator to "
                            "review the planning data."
                        )
                    )
                ],
            ),
        )

    def _validated_session(self, report: InventoryShortageReport) -> ConnectivitySession:
        session = self.workflows.get(report.workflow_id)
        if session is None:
            raise ValueError("inventory report references an unknown workflow")
        if report.correlation_id != session.correlation_id:
            raise ValueError("inventory report correlation ID does not match the workflow")
        if report.reply_to != str(session.request_message_id):
            raise ValueError("inventory report does not answer the pending planning request")
        return session

    def _record_inventory_delivery(self, request: DemandPlanningRequest, status: str) -> None:
        del request, status


class ConnectivityError(RuntimeError):
    """A bounded connectivity failure safe to show to the chat user."""
