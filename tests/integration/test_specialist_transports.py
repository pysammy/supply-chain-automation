import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

import pytest
from uagents import Model, Protocol

from supplymesh.agents.acknowledgment import (
    AcknowledgmentAgentService,
    build_acknowledgment_protocol,
)
from supplymesh.agents.procurement import ProcurementAgentService, build_procurement_protocol
from supplymesh.agents.sourcing import SourcingAgentService, build_sourcing_protocol
from supplymesh.agents.transport import (
    AcknowledgmentComparisonTransport,
    AcknowledgmentReceivedTransport,
    ChangeOrderSubmittedTransport,
    ChangeResponseReceivedTransport,
    CounterproposalExecutionTransport,
    ProcurementPlansReadyTransport,
    PurchaseOrderExecutionTransport,
    PurchaseOrderSubmittedTransport,
    SourcingRequestTransport,
    SpecialistFailureTransport,
    SupplierQuotesCollectedTransport,
    WorkflowStatusResponseTransport,
)
from supplymesh.domain.procurement import (
    ApprovalRecord,
    ChangeTransactionStatus,
    Counterproposal,
    CounterproposalLine,
    PurchaseOrder,
    PurchaseOrderLine,
    SupplierTransactionAccepted,
    SupplierTransactionStatus,
)
from supplymesh.domain.sourcing import QuoteLine, RfqRequest, SupplierQuote
from supplymesh.edi.x12_855 import AcknowledgmentLine, generate_855
from supplymesh.edi.x12_865 import ChangeResponseLine, generate_865
from supplymesh.protocols.procurement import (
    AcknowledgmentReceived,
    ChangeResponseReceived,
    CounterproposalExecutionRequest,
    PurchaseOrderExecutionRequest,
)
from supplymesh.protocols.sourcing import SourcingRequest
from supplymesh.services.approval_service import approval_payload_hash


class Context:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []
        self.logger = logging.getLogger("specialist-transport-test")

    async def send(self, destination: str, message: object, timeout: int = 30) -> None:
        del timeout
        self.sent.append((destination, message))


def handler(protocol: Protocol, model: type[Model]) -> Any:
    return protocol.signed_message_handlers[Model.build_schema_digest(model)]


class Gateway:
    async def submit_rfq(
        self,
        supplier_id: str,
        request: RfqRequest,
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> SupplierQuote:
        del idempotency_key, correlation_id
        line = request.lines[0]
        price = Decimal("10") if supplier_id == "alpha" else Decimal("11")
        return SupplierQuote(
            supplier_id=supplier_id,
            supplier_quote_ref=f"Q-{supplier_id}",
            currency="USD",
            valid_until=request.valid_until,
            reliability_score=Decimal("0.95"),
            lines=(
                QuoteLine(
                    line_number=1,
                    component_sku=line.component_sku,
                    offered_quantity=line.quantity,
                    unit_price=price,
                    promised_date=date(2026, 11, 20),
                    lead_time_days=10,
                    minimum_order_quantity=Decimal("1"),
                ),
            ),
        )


class Fx:
    async def resolve(
        self, source_currencies: set[str], target_currency: str
    ) -> dict[str, Decimal]:
        assert source_currencies == {"USD"}
        assert target_currency == "USD"
        return {"USD": Decimal("1")}


def sourcing_request() -> SourcingRequest:
    return SourcingRequest(
        correlation_id=uuid4(),
        workflow_id=uuid4(),
        sender_agent="manufacturing-orchestrator",
        shortages={"ENGINE-001": 75},
        required_by={"ENGINE-001": "2026-12-01"},
    )


@pytest.mark.asyncio
async def test_sourcing_transport_returns_correlated_quotes_and_plans() -> None:
    service = SourcingAgentService(Gateway(), ("alpha", "beta"), minimum_valid_quotes=2)
    protocol = build_sourcing_protocol(service, Fx())
    request = sourcing_request()
    wire = SourcingRequestTransport.from_domain(request)
    context = Context()

    await handler(protocol, SourcingRequestTransport)(context, "agent1orchestrator", wire)

    assert len(context.sent) == 2
    assert all(destination == "agent1orchestrator" for destination, _ in context.sent)
    quote_wire = context.sent[0][1]
    plans_wire = context.sent[1][1]
    assert isinstance(quote_wire, SupplierQuotesCollectedTransport)
    assert isinstance(plans_wire, ProcurementPlansReadyTransport)
    quotes = quote_wire.to_domain()
    plans = plans_wire.to_domain()
    assert quotes.reply_to == str(request.message_id)
    assert plans.reply_to == str(quotes.message_id)
    assert plans.correlation_id == request.correlation_id
    assert {plan.name for plan in plans.result.plans} == {
        "cheapest",
        "fastest",
        "balanced",
        "risk_aware",
        "split",
    }


@pytest.mark.asyncio
async def test_sourcing_transport_rejects_tampered_envelope_with_bounded_failure() -> None:
    service = SourcingAgentService(Gateway(), ("alpha", "beta"), minimum_valid_quotes=2)
    protocol = build_sourcing_protocol(service, Fx())
    wire = SourcingRequestTransport.from_domain(sourcing_request()).copy(
        update={"workflow_id": str(uuid4())}
    )
    context = Context()

    await handler(protocol, SourcingRequestTransport)(context, "agent1orchestrator", wire)

    assert len(context.sent) == 1
    failure = context.sent[0][1]
    assert isinstance(failure, SpecialistFailureTransport)
    assert failure.code == "SOURCING_FAILED"
    assert "Traceback" not in failure.message


class ProcurementGateway:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def submit_purchase_order(
        self, supplier_id: str, submission: object, *, idempotency_key: str, correlation_id: str
    ) -> SupplierTransactionAccepted:
        del supplier_id, submission, idempotency_key, correlation_id
        self.events.append("external_po")
        return SupplierTransactionAccepted(
            supplier_transaction_id="TX-PO",
            poll_url="/purchase-orders/TX-PO",
            estimated_ready_at=datetime(2026, 10, 16, tzinfo=UTC),
        )

    async def get_purchase_order_status(
        self, supplier_id: str, transaction_id: str, *, correlation_id: str
    ) -> SupplierTransactionStatus:
        del supplier_id, transaction_id, correlation_id
        return SupplierTransactionStatus(
            status="ACKNOWLEDGED",
            document_type="855",
            x12=generate_855(
                po_number="PO-TRANSPORT-1",
                po_version=1,
                currency="USD",
                lines=(
                    AcknowledgmentLine(
                        line_number=1,
                        component_sku="ENGINE-001",
                        status_code="IA",
                        accepted_quantity=Decimal("75"),
                        unit="EA",
                        unit_price=Decimal("10"),
                        promised_date=date(2026, 12, 1),
                    ),
                ),
                control_number="1",
            ),
        )

    async def submit_change_order(
        self, supplier_id: str, submission: object, *, idempotency_key: str, correlation_id: str
    ) -> SupplierTransactionAccepted:
        del supplier_id, submission, idempotency_key, correlation_id
        self.events.append("external_change")
        return SupplierTransactionAccepted(
            supplier_transaction_id="TX-CHANGE",
            poll_url="/change-orders/TX-CHANGE",
            estimated_ready_at=datetime(2026, 10, 16, tzinfo=UTC),
        )

    async def get_change_order_status(
        self, supplier_id: str, transaction_id: str, *, correlation_id: str
    ) -> ChangeTransactionStatus:
        del supplier_id, transaction_id, correlation_id
        return ChangeTransactionStatus(
            status="ACKNOWLEDGED",
            document_type="865",
            x12=generate_865(
                po_number="PO-TRANSPORT-1",
                po_version=2,
                currency="USD",
                lines=(
                    ChangeResponseLine(
                        line_number=1,
                        component_sku="ENGINE-001",
                        response_code="IA",
                        quantity=Decimal("75"),
                        unit="EA",
                        unit_price=Decimal("10"),
                        promised_date=date(2026, 12, 1),
                    ),
                ),
                control_number="2",
            ),
        )


class Journal:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.values: dict[str, dict[str, str]] = {}

    def find_success(self, key: str) -> dict[str, str] | None:
        return self.values.get(key)

    def record_pending(self, key: str, operation: str, payload_hash: str) -> None:
        del key, operation, payload_hash
        self.events.append("journal_pending")

    def record_success(self, key: str, result: dict[str, str]) -> None:
        self.events.append("journal_success")
        self.values[key] = result


class ProcurementState:
    def __init__(self, approvals: dict[str, ApprovalRecord], events: list[str]) -> None:
        self.approvals = approvals
        self.events = events

    def get_approval(self, approval_id: str) -> ApprovalRecord:
        return self.approvals[approval_id]

    def save_approved_purchase_order(self, po: PurchaseOrder) -> None:
        del po
        self.events.append("persist_po")

    def record_submission(
        self, po: PurchaseOrder, *, external_transaction_id: str, poll_url: str
    ) -> None:
        del po, external_transaction_id, poll_url
        self.events.append("persist_submission")

    def record_purchase_order_intent(
        self,
        po: PurchaseOrder,
        *,
        correlation_id: UUID,
        request_message_id: UUID,
        response_message_id: UUID,
        approval_id: UUID,
        reply_address: str,
    ) -> None:
        del (
            po,
            correlation_id,
            request_message_id,
            response_message_id,
            approval_id,
            reply_address,
        )
        self.events.append("persist_po_intent")

    def record_change_order_intent(
        self,
        counterproposal: Counterproposal,
        *,
        correlation_id: UUID,
        request_message_id: UUID,
        response_message_id: UUID,
        approval_id: UUID,
        reply_address: str,
    ) -> None:
        del (
            counterproposal,
            correlation_id,
            request_message_id,
            response_message_id,
            approval_id,
            reply_address,
        )
        self.events.append("persist_change_intent")

    def record_change_submission(
        self,
        counterproposal: Counterproposal,
        *,
        external_transaction_id: str,
        poll_url: str,
    ) -> None:
        del counterproposal, external_transaction_id, poll_url
        self.events.append("persist_change_submission")

    def mark_transaction_ready(self, supplier_id: str, idempotency_key: str) -> None:
        del supplier_id, idempotency_key
        self.events.append("persist_ready")

    def mark_response_delivered(self, response_message_id: UUID) -> None:
        del response_message_id
        self.events.append("persist_delivered")

    def claim_pending_transactions(
        self, owner: str, *, lease_seconds: float = 30
    ) -> tuple[object, ...]:
        del owner, lease_seconds
        return ()

    def release_claim(self, response_message_id: UUID, owner: str) -> None:
        del response_message_id, owner

    def pending_transactions(self) -> tuple[object, ...]:
        return ()

    def record_edi_document(
        self,
        *,
        workflow_id: UUID,
        supplier_id: str,
        document_type: Literal["850", "855", "860", "865"],
        direction: Literal["OUTBOUND", "INBOUND"],
        raw_text: str,
        related_document_id: UUID,
    ) -> UUID:
        del workflow_id, supplier_id, direction, raw_text
        self.events.append(f"persist_{document_type}")
        return related_document_id

    def record_change_order(self, counterproposal: Counterproposal, *, approval_id: UUID) -> UUID:
        del approval_id
        self.events.append("persist_change")
        return counterproposal.po_id


def purchase_order() -> PurchaseOrder:
    return PurchaseOrder(
        po_id=uuid4(),
        po_number="PO-TRANSPORT-1",
        workflow_id=uuid4(),
        supplier_id="supplier-alpha",
        version=1,
        currency="USD",
        lines=(
            PurchaseOrderLine(
                line_number=1,
                component_sku="ENGINE-001",
                quantity=Decimal("75"),
                unit_price=Decimal("10"),
                requested_date=date(2026, 12, 1),
            ),
        ),
        total=Decimal("750"),
    )


def approval(target: PurchaseOrder | Counterproposal, decision_type: str) -> ApprovalRecord:
    po_id = target.po_id
    version = target.version if isinstance(target, PurchaseOrder) else target.current_po_version
    return ApprovalRecord(
        approval_id=uuid4(),
        workflow_id=target.workflow_id,
        decision_type=decision_type,
        target_type="purchase_order",
        target_id=str(po_id),
        target_version=version,
        decision="APPROVED",
        actor="manager",
        payload_hash=approval_payload_hash(target),
        decided_at=datetime(2026, 10, 16, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_procurement_transport_persists_executes_and_forwards_855() -> None:
    events: list[str] = []
    po = purchase_order()
    approved = approval(po, "SEND_PURCHASE_ORDER")
    service = ProcurementAgentService(
        ProcurementGateway(events),
        Journal(events),
        ProcurementState({str(approved.approval_id): approved}, events),
        poll_interval_seconds=0.01,
        poll_timeout_seconds=0.02,
    )
    request = PurchaseOrderExecutionRequest(
        correlation_id=uuid4(),
        workflow_id=po.workflow_id,
        sender_agent="manufacturing-orchestrator",
        purchase_orders=(po,),
        approval_id=str(approved.approval_id),
    )
    context = Context()
    await handler(build_procurement_protocol(service), PurchaseOrderExecutionTransport)(
        context, "agent1orchestrator", PurchaseOrderExecutionTransport.from_domain(request)
    )

    assert isinstance(context.sent[0][1], PurchaseOrderSubmittedTransport)
    assert isinstance(context.sent[1][1], AcknowledgmentReceivedTransport)
    acknowledgment = context.sent[1][1].to_domain()
    assert acknowledgment.po_id == str(po.po_id)
    assert acknowledgment.reply_to == str(context.sent[0][1].to_domain().message_id)
    assert events == [
        "persist_po",
        "persist_850",
        "persist_po_intent",
        "journal_pending",
        "external_po",
        "journal_success",
        "persist_submission",
        "persist_855",
        "persist_ready",
        "persist_delivered",
    ]


@pytest.mark.asyncio
async def test_procurement_transport_executes_approved_counterproposal_and_forwards_865() -> None:
    events: list[str] = []
    po = purchase_order()
    counter = Counterproposal(
        workflow_id=po.workflow_id,
        po_id=po.po_id,
        po_number=po.po_number,
        current_po_version=1,
        change_version=2,
        supplier_id=po.supplier_id,
        currency=po.currency,
        lines=(
            CounterproposalLine(
                line_number=1,
                component_sku="ENGINE-001",
                quantity=Decimal("75"),
                unit="EA",
                maximum_unit_price=Decimal("10"),
                requested_date=date(2026, 12, 1),
            ),
        ),
    )
    approved = approval(counter, "SEND_COUNTERPROPOSAL")
    service = ProcurementAgentService(
        ProcurementGateway(events),
        Journal(events),
        ProcurementState({str(approved.approval_id): approved}, events),
        poll_interval_seconds=0.01,
        poll_timeout_seconds=0.02,
    )
    request = CounterproposalExecutionRequest(
        correlation_id=uuid4(),
        workflow_id=po.workflow_id,
        sender_agent="manufacturing-orchestrator",
        counterproposal=counter,
        approval_id=str(approved.approval_id),
    )
    context = Context()
    await handler(build_procurement_protocol(service), CounterproposalExecutionTransport)(
        context, "agent1orchestrator", CounterproposalExecutionTransport.from_domain(request)
    )

    assert isinstance(context.sent[0][1], ChangeOrderSubmittedTransport)
    assert isinstance(context.sent[1][1], ChangeResponseReceivedTransport)
    assert context.sent[1][1].to_domain().po_version == 2
    assert events == [
        "persist_change",
        "persist_860",
        "persist_change_intent",
        "journal_pending",
        "external_change",
        "journal_success",
        "persist_change_submission",
        "persist_865",
        "persist_ready",
        "persist_delivered",
    ]


class PurchaseOrders:
    def __init__(self, po: PurchaseOrder) -> None:
        self.po = po

    def get_purchase_order(self, po_id: str) -> PurchaseOrder:
        assert po_id == str(self.po.po_id)
        return self.po


@pytest.mark.asyncio
async def test_acknowledgment_transport_compares_855_and_evaluates_865() -> None:
    po = purchase_order()
    service = AcknowledgmentAgentService(PurchaseOrders(po))
    protocol = build_acknowledgment_protocol(service)
    acknowledgment = AcknowledgmentReceived(
        correlation_id=uuid4(),
        workflow_id=po.workflow_id,
        sender_agent="procurement-execution",
        reply_to=str(uuid4()),
        po_id=str(po.po_id),
        po_version=po.version,
        raw_x12=generate_855(
            po_number=po.po_number,
            po_version=po.version,
            currency=po.currency,
            lines=(
                AcknowledgmentLine(
                    line_number=1,
                    component_sku="ENGINE-001",
                    status_code="IA",
                    accepted_quantity=Decimal("75"),
                    unit="EA",
                    unit_price=Decimal("10"),
                    promised_date=date(2026, 12, 1),
                ),
            ),
            control_number="3",
        ),
    )
    context = Context()
    await handler(protocol, AcknowledgmentReceivedTransport)(
        context,
        "agent1procurement",
        AcknowledgmentReceivedTransport.from_domain(acknowledgment),
    )
    result = context.sent[0][1]
    assert isinstance(result, AcknowledgmentComparisonTransport)
    assert result.to_domain().comparison.status == "MATCHED"

    change = ChangeResponseReceived(
        correlation_id=acknowledgment.correlation_id,
        workflow_id=po.workflow_id,
        sender_agent="procurement-execution",
        reply_to=str(uuid4()),
        po_id=str(po.po_id),
        po_version=2,
        raw_x12=generate_865(
            po_number=po.po_number,
            po_version=2,
            currency=po.currency,
            lines=(
                ChangeResponseLine(
                    line_number=1,
                    component_sku="ENGINE-001",
                    response_code="IA",
                    quantity=Decimal("75"),
                    unit="EA",
                    unit_price=Decimal("10"),
                    promised_date=date(2026, 12, 1),
                ),
            ),
            control_number="4",
        ),
    )
    change_context = Context()
    await handler(protocol, ChangeResponseReceivedTransport)(
        change_context,
        "agent1procurement",
        ChangeResponseReceivedTransport.from_domain(change),
    )
    status = change_context.sent[0][1]
    assert isinstance(status, WorkflowStatusResponseTransport)
    assert status.to_domain().status == "CHANGE_ACCEPTED"
    assert status.to_domain().summary["accepted"] is True


@pytest.mark.asyncio
async def test_acknowledgment_transport_returns_bounded_failure_for_malformed_edi() -> None:
    po = purchase_order()
    service = AcknowledgmentAgentService(PurchaseOrders(po))
    message = AcknowledgmentReceived(
        correlation_id=uuid4(),
        workflow_id=po.workflow_id,
        sender_agent="procurement-execution",
        reply_to=str(uuid4()),
        po_id=str(po.po_id),
        po_version=po.version,
        raw_x12="not-x12",
    )
    context = Context()
    await handler(build_acknowledgment_protocol(service), AcknowledgmentReceivedTransport)(
        context,
        "agent1procurement",
        AcknowledgmentReceivedTransport.from_domain(message),
    )
    failure = context.sent[0][1]
    assert isinstance(failure, SpecialistFailureTransport)
    assert failure.code == "ACKNOWLEDGMENT_PROCESSING_FAILED"
    assert "Traceback" not in failure.message
