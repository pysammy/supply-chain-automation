import json
import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from uagents import Model, Protocol
from uagents_core.contrib.protocols.chat import ChatMessage, MetadataContent, TextContent

from supplymesh.agents.orchestration import (
    OrchestrationCoordinator,
    build_specialist_response_protocol,
)
from supplymesh.agents.orchestrator import build_chat_protocol, build_planning_response_protocol
from supplymesh.agents.transport import (
    AcknowledgmentComparisonTransport,
    AcknowledgmentReceivedTransport,
    ChangeOrderSubmittedTransport,
    ChangeResponseReceivedTransport,
    CounterproposalExecutionTransport,
    InventoryShortageTransport,
    ProcurementPlansReadyTransport,
    PurchaseOrderExecutionTransport,
    PurchaseOrderSubmittedTransport,
    SourcingRequestTransport,
    SupplierQuotesCollectedTransport,
    WorkflowStatusResponseTransport,
)
from supplymesh.cards.renderer import CardSelection
from supplymesh.domain.exceptions import AcknowledgmentComparison
from supplymesh.domain.models import InventoryAssessment, ShortageLine
from supplymesh.domain.sourcing import OptimizationResult, PlanAllocation, ProcurementPlan
from supplymesh.persistence.orm_models import (
    ApprovalDecisionRecord,
    WorkflowRun,
)
from supplymesh.persistence.workflow_repository import ConnectivitySession, WorkflowRepository
from supplymesh.protocols.common import WorkflowStatusResponse
from supplymesh.protocols.exceptions import AcknowledgmentComparisonResult
from supplymesh.protocols.planning import InventoryShortageReport
from supplymesh.protocols.procurement import (
    AcknowledgmentReceived,
    ChangeOrderSubmitted,
    ChangeResponseReceived,
    PurchaseOrderSubmitted,
)
from supplymesh.protocols.sourcing import (
    ProcurementPlansReady,
    SourcingRequest,
    SupplierQuotesCollected,
)
from supplymesh.services.order_factory import purchase_orders_from_plan
from supplymesh.workflows.procurement_graph import build_event_transition_graph


class Context:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []
        self.logger = logging.getLogger("orchestrator-routing-test")

    async def send(self, destination: str, message: object, timeout: int = 30) -> None:
        del timeout
        self.sent.append((destination, message))


class Requests:
    pass


class ActivityAudit:
    def log_message(self, **values: object) -> None:
        del values

    def list_activity(self, workflow_id: object) -> list[dict[str, str]]:
        del workflow_id
        return [
            {
                "kind": "message",
                "agent": "inventory-and-bom",
                "action": "InventoryShortageReport",
                "outcome": "RECEIVED",
            }
        ]


def handler(protocol: Protocol, model: type[Model]) -> Any:
    return protocol.signed_message_handlers[Model.build_schema_digest(model)]


def repository() -> tuple[Any, WorkflowRepository, Any]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    WorkflowRun.__table__.create(engine)
    ApprovalDecisionRecord.__table__.create(engine)

    def sessions() -> Session:
        return Session(engine)

    return engine, WorkflowRepository(sessions), sessions


def chat_selection(action: str, target_id: str, target_version: int) -> ChatMessage:
    return ChatMessage(
        timestamp=datetime(2026, 10, 16, tzinfo=UTC),
        content=[
            TextContent(
                text=json.dumps(
                    {
                        "action": action,
                        "target_id": target_id,
                        "target_version": target_version,
                    }
                )
            )
        ],
    )


@pytest.mark.asyncio
async def test_orchestrator_routes_all_specialists_and_completes_exact_ack() -> None:
    engine, store, sessions = repository()
    workflow_id = uuid4()
    correlation_id = uuid4()
    inventory_request_id = uuid4()
    store.start_inventory(
        ConnectivitySession(
            workflow_id=workflow_id,
            conversation_id="agent1manager",
            correlation_id=correlation_id,
            request_message_id=inventory_request_id,
            chat_message_id=uuid4(),
            status="INVENTORY_PLANNING",
            currency="USD",
        )
    )
    coordinator = OrchestrationCoordinator(
        inventory_address="agent1inventory",
        requests=Requests(),  # type: ignore[arg-type]
        workflows=store,
        sourcing_address="agent1sourcing",
        procurement_address="agent1procurement",
        acknowledgment_address="agent1ack",
        transition_graph=build_event_transition_graph(),
        audit=ActivityAudit(),  # type: ignore[arg-type]
    )
    specialist_protocol = build_specialist_response_protocol(coordinator)
    planning_protocol = build_planning_response_protocol(coordinator)
    chat_protocol = build_chat_protocol(coordinator)

    assessment = InventoryAssessment(
        demand_order_id=workflow_id,
        product_sku="MOTO-URBANX-2026",
        demand_quantity=Decimal("75"),
        required_by=date(2026, 12, 1),
        lines=(
            ShortageLine(
                component_sku="ENGINE-001",
                unit="EA",
                gross_required=Decimal("75.5"),
                available_to_promise=Decimal("20"),
                tentative_reservation=Decimal("20"),
                shortage=Decimal("55.5"),
                evidence=("protected existing reservations",),
            ),
        ),
        has_shortages=True,
        calculated_at=datetime(2026, 10, 16, tzinfo=UTC),
    )
    inventory_report = InventoryShortageReport(
        correlation_id=correlation_id,
        workflow_id=workflow_id,
        sender_agent="inventory-and-bom",
        reply_to=str(inventory_request_id),
        assessment=assessment,
        tentative_reservations=(),
    )
    inventory_context = Context()
    await handler(planning_protocol, InventoryShortageTransport)(
        inventory_context,
        "agent1inventory",
        InventoryShortageTransport.from_domain(inventory_report),
    )
    sourcing_wire = next(
        message
        for _, message in inventory_context.sent
        if isinstance(message, SourcingRequestTransport)
    )
    sourcing_request = sourcing_wire.to_domain()
    assert sourcing_request.shortages == {"ENGINE-001": 56}

    quotes = SupplierQuotesCollected(
        correlation_id=correlation_id,
        workflow_id=workflow_id,
        sender_agent="sourcing-and-optimization",
        reply_to=str(sourcing_request.message_id),
        quotes=(),
        missing_suppliers=(),
    )
    plans = ProcurementPlansReady(
        correlation_id=correlation_id,
        workflow_id=workflow_id,
        sender_agent="sourcing-and-optimization",
        reply_to=str(quotes.message_id),
        result=OptimizationResult(
            plans=(
                ProcurementPlan(
                    plan_id="cheapest-test",
                    name="cheapest",
                    allocations=(
                        PlanAllocation(
                            component_sku="ENGINE-001",
                            supplier_id="supplier-alpha",
                            quote_ref="quote-alpha",
                            quantity=56,
                            unit_price_minor=1000,
                            promised_date=date(2026, 11, 20),
                        ),
                    ),
                    total_cost_minor=56_000,
                    currency="USD",
                    latest_arrival=date(2026, 11, 20),
                    total_late_days=0,
                    risk_score_basis_points=500,
                    supplier_count=1,
                ),
            )
        ),
    )
    sourcing_context = Context()
    await handler(specialist_protocol, SupplierQuotesCollectedTransport)(
        sourcing_context,
        "agent1sourcing",
        SupplierQuotesCollectedTransport.from_domain(quotes),
    )
    await handler(specialist_protocol, ProcurementPlansReadyTransport)(
        sourcing_context,
        "agent1sourcing",
        ProcurementPlansReadyTransport.from_domain(plans),
    )
    sent_after_first_plan = len(sourcing_context.sent)
    await handler(specialist_protocol, ProcurementPlansReadyTransport)(
        sourcing_context,
        "agent1sourcing",
        ProcurementPlansReadyTransport.from_domain(plans),
    )
    assert len(sourcing_context.sent) == sent_after_first_plan
    assert store.snapshot(workflow_id).status == "WAITING_FOR_PLAN_APPROVAL"  # type: ignore[union-attr]

    review_context = Context()
    await handler(chat_protocol, ChatMessage)(
        review_context,
        "agent1manager",
        chat_selection("review_plan", "cheapest-test", 1),
    )
    approve_context = Context()
    await handler(chat_protocol, ChatMessage)(
        approve_context,
        "agent1manager",
        chat_selection("approve_plan", "cheapest-test", 1),
    )
    snapshot = store.snapshot(workflow_id)
    assert snapshot is not None
    batch_id = snapshot.state["purchase_order_batch_id"]

    po_approval_context = Context()
    await handler(chat_protocol, ChatMessage)(
        po_approval_context,
        "agent1manager",
        chat_selection("approve_and_send_purchase_orders", batch_id, 1),
    )
    po_wire = next(
        message
        for _, message in po_approval_context.sent
        if isinstance(message, PurchaseOrderExecutionTransport)
    )
    po_request = po_wire.to_domain()
    po = po_request.purchase_orders[0]
    assert po_request.approval_ids

    submitted = PurchaseOrderSubmitted(
        correlation_id=correlation_id,
        workflow_id=workflow_id,
        sender_agent="procurement-execution",
        reply_to=str(po_request.message_id),
        po_id=str(po.po_id),
        supplier_transaction_id="txn-1",
        poll_url="/poll/txn-1",
    )
    procurement_context = Context()
    await handler(specialist_protocol, PurchaseOrderSubmittedTransport)(
        procurement_context,
        "agent1procurement",
        PurchaseOrderSubmittedTransport.from_domain(submitted),
    )
    acknowledgment = AcknowledgmentReceived(
        correlation_id=correlation_id,
        workflow_id=workflow_id,
        sender_agent="procurement-execution",
        reply_to=str(submitted.message_id),
        po_id=str(po.po_id),
        po_version=po.version,
        raw_x12="ST*855*1~",
    )
    await handler(specialist_protocol, AcknowledgmentReceivedTransport)(
        procurement_context,
        "agent1procurement",
        AcknowledgmentReceivedTransport.from_domain(acknowledgment),
    )
    forwarded_ack = next(
        message.to_domain()
        for destination, message in procurement_context.sent
        if destination == "agent1ack" and isinstance(message, AcknowledgmentReceivedTransport)
    )
    assert forwarded_ack.message_id != acknowledgment.message_id
    assert forwarded_ack.reply_to == str(acknowledgment.message_id)

    comparison = AcknowledgmentComparisonResult(
        correlation_id=correlation_id,
        workflow_id=workflow_id,
        sender_agent="acknowledgment-and-exception",
        reply_to=str(forwarded_ack.message_id),
        po_id=str(po.po_id),
        po_version=po.version,
        comparison=AcknowledgmentComparison(
            status="MATCHED",
            discrepancies=(),
            absolute_cost_delta=Decimal("0"),
            percentage_cost_delta=Decimal("0"),
            unfulfilled_quantity=Decimal("0"),
            production_units_affected=Decimal("0"),
            estimated_schedule_delay_days=0,
            recommended_action="complete",
            allowed_actions=(),
        ),
    )
    completion_context = Context()
    await handler(specialist_protocol, AcknowledgmentComparisonTransport)(
        completion_context,
        "agent1ack",
        AcknowledgmentComparisonTransport.from_domain(comparison),
    )

    completed = store.snapshot(workflow_id)
    assert completed is not None
    assert completed.status == "COMPLETED"
    assert any(
        destination == "agent1manager" and isinstance(message, ChatMessage)
        for destination, message in completion_context.sent
    )
    activity_cards = [
        json.loads(content.metadata["card_payload"])
        for _, message in completion_context.sent
        if isinstance(message, ChatMessage)
        for content in message.content
        if isinstance(content, MetadataContent)
        and content.metadata.get("card_kind") == "custom"
        and json.loads(content.metadata["card_payload"]).get("title") == "Agent activity"
    ]
    assert activity_cards[0]["activities"][0]["agent"] == "inventory-and-bom"
    assert all(
        item["status"] == "DELIVERED" for item in completed.state["outbound_messages"].values()
    )
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ApprovalDecisionRecord)) == 2
    engine.dispose()


@pytest.mark.asyncio
async def test_conflict_counterproposal_routes_860_and_865_back_to_completion() -> None:
    engine, store, _ = repository()
    workflow_id = uuid4()
    correlation_id = uuid4()
    plan = ProcurementPlan(
        plan_id="counter-plan",
        name="cheapest",
        allocations=(
            PlanAllocation(
                component_sku="ENGINE-001",
                supplier_id="supplier-alpha",
                quote_ref="quote-alpha",
                quantity=55,
                unit_price_minor=1000,
                promised_date=date(2026, 11, 20),
            ),
        ),
        total_cost_minor=55_000,
        currency="USD",
        latest_arrival=date(2026, 11, 20),
        total_late_days=0,
        risk_score_basis_points=500,
        supplier_count=1,
    )
    po = purchase_orders_from_plan(workflow_id, plan)[0]
    comparison = AcknowledgmentComparisonResult(
        correlation_id=correlation_id,
        workflow_id=workflow_id,
        sender_agent="acknowledgment-and-exception",
        reply_to=str(uuid4()),
        po_id=str(po.po_id),
        po_version=1,
        comparison=AcknowledgmentComparison(
            status="EXCEPTION",
            discrepancies=(),
            absolute_cost_delta=Decimal("10"),
            percentage_cost_delta=Decimal("1"),
            unfulfilled_quantity=Decimal("0"),
            production_units_affected=Decimal("0"),
            estimated_schedule_delay_days=0,
            recommended_action="counter",
            allowed_actions=("accept", "counter", "cancel"),
        ),
    )
    with store.session_factory() as session, session.begin():
        session.add(
            WorkflowRun(
                id=workflow_id,
                conversation_id="agent1manager",
                status="WAITING_FOR_EXCEPTION_DECISION",
                checkpoint_version=1,
                state={
                    "correlation_id": str(correlation_id),
                    "currency": "USD",
                    "plans": [plan.model_dump(mode="json")],
                    "selected_plan_id": plan.plan_id,
                    "purchase_orders": [po.model_dump(mode="json")],
                    "comparisons": {str(po.po_id): comparison.model_dump(mode="json")},
                },
            )
        )
    coordinator = OrchestrationCoordinator(
        inventory_address="agent1inventory",
        requests=Requests(),  # type: ignore[arg-type]
        workflows=store,
        sourcing_address="agent1sourcing",
        procurement_address="agent1procurement",
        acknowledgment_address="agent1ack",
        transition_graph=build_event_transition_graph(),
    )
    protocol = build_specialist_response_protocol(coordinator)
    context = Context()
    await coordinator.handle_selection(
        context,
        conversation_id="agent1manager",
        selection=CardSelection(action="counter", target_id=str(po.po_id), target_version=1),
    )
    await coordinator.handle_selection(
        context,
        conversation_id="agent1manager",
        selection=CardSelection(
            action="review_counterproposal",
            target_id=str(po.po_id),
            target_version=1,
            values={
                "quantity": "55",
                "maximum_price": "10.50",
                "required_date": "2026-11-20",
            },
        ),
    )
    await coordinator.handle_selection(
        context,
        conversation_id="agent1manager",
        selection=CardSelection(
            action="approve_counterproposal",
            target_id=str(po.po_id),
            target_version=1,
        ),
    )
    request_wire = next(
        message
        for _, message in context.sent
        if isinstance(message, CounterproposalExecutionTransport)
    )
    request = request_wire.to_domain()
    submitted = ChangeOrderSubmitted(
        correlation_id=correlation_id,
        workflow_id=workflow_id,
        sender_agent="procurement-execution",
        reply_to=str(request.message_id),
        po_id=str(po.po_id),
        change_version=2,
        supplier_transaction_id="change-1",
    )
    await handler(protocol, ChangeOrderSubmittedTransport)(
        context,
        "agent1procurement",
        ChangeOrderSubmittedTransport.from_domain(submitted),
    )
    response = ChangeResponseReceived(
        correlation_id=correlation_id,
        workflow_id=workflow_id,
        sender_agent="procurement-execution",
        reply_to=str(submitted.message_id),
        po_id=str(po.po_id),
        po_version=2,
        raw_x12="ST*865*1~",
    )
    await handler(protocol, ChangeResponseReceivedTransport)(
        context,
        "agent1procurement",
        ChangeResponseReceivedTransport.from_domain(response),
    )
    forwarded_response = next(
        message.to_domain()
        for destination, message in context.sent
        if destination == "agent1ack" and isinstance(message, ChangeResponseReceivedTransport)
    )
    assert forwarded_response.message_id != response.message_id
    assert forwarded_response.reply_to == str(response.message_id)
    status = WorkflowStatusResponse(
        correlation_id=correlation_id,
        workflow_id=workflow_id,
        sender_agent="acknowledgment-and-exception",
        reply_to=str(forwarded_response.message_id),
        status="CHANGE_ACCEPTED",
        checkpoint_version=2,
        summary={"accepted": True},
    )
    await handler(protocol, WorkflowStatusResponseTransport)(
        context,
        "agent1ack",
        WorkflowStatusResponseTransport.from_domain(status),
    )
    completed = store.snapshot(workflow_id)
    assert completed is not None
    assert completed.status == "COMPLETED"
    engine.dispose()


@pytest.mark.asyncio
async def test_orchestrator_replays_durable_pending_outbound_with_stable_identity() -> None:
    engine, store, _ = repository()
    workflow_id = uuid4()
    correlation_id = uuid4()
    store.start_inventory(
        ConnectivitySession(
            workflow_id=workflow_id,
            conversation_id="agent1manager",
            correlation_id=correlation_id,
            request_message_id=uuid4(),
            chat_message_id=uuid4(),
            status="SOURCING",
        )
    )
    request = SourcingRequest(
        correlation_id=correlation_id,
        workflow_id=workflow_id,
        sender_agent="manufacturing-orchestrator",
        shortages={"ENGINE-001": 55},
        required_by={"ENGINE-001": "2026-12-01"},
    )
    store.record_outbound(
        workflow_id,
        message_id=request.message_id,
        destination="agent1sourcing",
        message_type=request.message_type,
        status="PENDING",
        payload_json=request.model_dump_json(),
    )
    coordinator = OrchestrationCoordinator(
        inventory_address="agent1inventory",
        requests=Requests(),  # type: ignore[arg-type]
        workflows=store,
        sourcing_address="agent1sourcing",
        transition_graph=build_event_transition_graph(),
    )
    protocol = build_specialist_response_protocol(coordinator, recovery_interval_seconds=0.01)
    interval_handler, interval_seconds = protocol.intervals[0]
    context = Context()

    await interval_handler(context)

    assert interval_seconds == 0.01
    assert context.sent[0][0] == "agent1sourcing"
    assert isinstance(context.sent[0][1], SourcingRequestTransport)
    assert context.sent[0][1].to_domain().message_id == request.message_id
    assert store.pending_outbound() == ()
    engine.dispose()
