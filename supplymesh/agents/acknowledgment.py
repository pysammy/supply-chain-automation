from typing import Protocol
from uuid import UUID

from pydantic import ValidationError
from uagents import Context
from uagents import Protocol as AgentProtocol

from supplymesh.agents.transport import (
    AcknowledgmentComparisonTransport,
    AcknowledgmentReceivedTransport,
    ChangeResponseReceivedTransport,
    SpecialistFailureTransport,
    WorkflowStatusResponseTransport,
)
from supplymesh.domain.exceptions import AcknowledgmentComparison
from supplymesh.domain.procurement import PurchaseOrder
from supplymesh.edi.x12_855 import Parsed855, parse_855
from supplymesh.edi.x12_865 import parse_865
from supplymesh.protocols.common import WorkflowStatusResponse
from supplymesh.protocols.exceptions import AcknowledgmentComparisonResult
from supplymesh.protocols.procurement import AcknowledgmentReceived, ChangeResponseReceived
from supplymesh.services.comparison_service import compare_acknowledgment


class PurchaseOrderLookup(Protocol):
    def get_purchase_order(self, po_id: str) -> PurchaseOrder: ...


class AcknowledgmentEvidence(Protocol):
    def record_acknowledgment_comparison(
        self,
        *,
        po: PurchaseOrder,
        parsed: Parsed855,
        comparison: AcknowledgmentComparison,
    ) -> UUID: ...

    def record_change_result(self, po_id: UUID, version: int, *, accepted: bool) -> None: ...


class AcknowledgmentAgentService:
    name = "acknowledgment-and-exception"

    def __init__(
        self,
        purchase_orders: PurchaseOrderLookup,
        evidence: AcknowledgmentEvidence | None = None,
    ) -> None:
        self.purchase_orders = purchase_orders
        self.evidence = evidence

    def compare(self, message: AcknowledgmentReceived) -> AcknowledgmentComparisonResult:
        po = self.purchase_orders.get_purchase_order(message.po_id)
        if message.po_version != po.version:
            raise ValueError("acknowledgment message refers to the wrong PO version")
        parsed = parse_855(message.raw_x12)
        if parsed.po_number != po.po_number:
            raise ValueError("acknowledgment PO number does not match the stored purchase order")
        comparison = compare_acknowledgment(po, parsed)
        if self.evidence is not None:
            self.evidence.record_acknowledgment_comparison(
                po=po,
                parsed=parsed,
                comparison=comparison,
            )
        return AcknowledgmentComparisonResult(
            correlation_id=message.correlation_id,
            workflow_id=message.workflow_id,
            sender_agent=self.name,
            reply_to=str(message.message_id),
            po_id=message.po_id,
            po_version=message.po_version,
            comparison=comparison,
        )

    def evaluate_change(self, message: ChangeResponseReceived) -> WorkflowStatusResponse:
        po = self.purchase_orders.get_purchase_order(message.po_id)
        parsed = parse_865(message.raw_x12)
        if parsed.po_number != po.po_number or parsed.po_version != message.po_version:
            raise ValueError("change response does not match the expected PO and version")
        if message.po_version <= po.version:
            raise ValueError("change response is stale")
        rejected_lines = tuple(
            line.line_number for line in parsed.lines if line.response_code != "IA"
        )
        accepted = bool(parsed.lines) and not rejected_lines
        if self.evidence is not None:
            self.evidence.record_change_result(
                po.po_id,
                message.po_version,
                accepted=accepted,
            )
        return WorkflowStatusResponse(
            correlation_id=message.correlation_id,
            workflow_id=message.workflow_id,
            sender_agent=self.name,
            reply_to=str(message.message_id),
            status="CHANGE_ACCEPTED" if accepted else "CHANGE_REJECTED",
            checkpoint_version=message.po_version,
            summary={
                "po_id": message.po_id,
                "po_number": parsed.po_number,
                "po_version": parsed.po_version,
                "accepted": accepted,
                "rejected_lines": rejected_lines,
            },
        )


def build_acknowledgment_protocol(service: AcknowledgmentAgentService) -> AgentProtocol:
    protocol = AgentProtocol(name="SupplyMeshAcknowledgmentException", version="1.0.0")

    @protocol.on_message(
        AcknowledgmentReceivedTransport,
        replies={AcknowledgmentComparisonTransport, SpecialistFailureTransport},
    )
    async def handle_acknowledgment(
        ctx: Context, sender: str, msg: AcknowledgmentReceivedTransport
    ) -> None:
        try:
            result = service.compare(msg.to_domain())
            await ctx.send(sender, AcknowledgmentComparisonTransport.from_domain(result))
        except (ValidationError, ValueError):
            await _send_failure(ctx, sender, msg, service.name, "ACKNOWLEDGMENT_COMPARISON")

    @protocol.on_message(
        ChangeResponseReceivedTransport,
        replies={WorkflowStatusResponseTransport, SpecialistFailureTransport},
    )
    async def handle_change_response(
        ctx: Context, sender: str, msg: ChangeResponseReceivedTransport
    ) -> None:
        try:
            result = service.evaluate_change(msg.to_domain())
            await ctx.send(sender, WorkflowStatusResponseTransport.from_domain(result))
        except (ValidationError, ValueError):
            await _send_failure(ctx, sender, msg, service.name, "CHANGE_RESPONSE_COMPARISON")

    return protocol


async def _send_failure(
    ctx: Context,
    sender: str,
    msg: AcknowledgmentReceivedTransport | ChangeResponseReceivedTransport,
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
            code="ACKNOWLEDGMENT_PROCESSING_FAILED",
            message="The supplier response could not be matched and validated safely.",
            retryable=False,
        ),
    )
