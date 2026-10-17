import json
import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from uagents import Model, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    MetadataContent,
    TextContent,
)
from uagents_core.types import DeliveryStatus

from supplymesh.agents.connectivity import FetchConnectivityCoordinator
from supplymesh.agents.inventory import InventoryAgentService, build_inventory_protocol
from supplymesh.agents.orchestrator import build_chat_protocol, build_planning_response_protocol
from supplymesh.agents.transport import (
    DemandPlanningTransport,
    InventoryShortageTransport,
    PlanningFailureTransport,
)
from supplymesh.cards.demand import DemandIntake
from supplymesh.domain.enums import Priority
from supplymesh.domain.models import BomComponent, Demand, InventoryPosition
from supplymesh.persistence.orm_models import (
    Bom,
    BomItem,
    DemandOrder,
    InboundSupply,
    InventoryBalance,
    InventoryReservation,
    Product,
    WorkflowRun,
)
from supplymesh.persistence.planning_repository import PlanningRequestRepository
from supplymesh.persistence.workflow_repository import ConnectivitySession, WorkflowRepository
from supplymesh.protocols.planning import DemandPlanningRequest, InventoryShortageReport


class FakeContext:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []
        self.logger = logging.getLogger("fetch-connectivity-test")

    async def send(self, destination: str, message: object, timeout: int = 30) -> None:
        del timeout
        self.sent.append((destination, message))


class FailedDeliveryContext(FakeContext):
    async def send(self, destination: str, message: object, timeout: int = 30) -> Any:
        await super().send(destination, message, timeout)
        return type("FailedDelivery", (), {"status": DeliveryStatus.FAILED})()


class FakeRequestFactory:
    def create_request(
        self,
        intake: DemandIntake,
        *,
        workflow_id: UUID,
        correlation_id: UUID,
    ) -> DemandPlanningRequest:
        return DemandPlanningRequest(
            correlation_id=correlation_id,
            workflow_id=workflow_id,
            sender_agent="manufacturing-orchestrator",
            demand=Demand(
                demand_order_id=workflow_id,
                product_sku=intake.product_sku,
                quantity=Decimal(intake.quantity),
                required_by=intake.required_by,
                priority=Priority(intake.priority),
            ),
            bom=(
                BomComponent(
                    component_sku="ENGINE-001",
                    quantity_per=Decimal("1"),
                    scrap_factor=Decimal("0"),
                    unit="EA",
                ),
            ),
            inventory_positions=(
                InventoryPosition(
                    component_sku="ENGINE-001",
                    on_hand=Decimal("130"),
                    existing_reservations=Decimal("100"),
                    safety_stock=Decimal("10"),
                    unit="EA",
                ),
            ),
        )


class FakeWorkflowStore:
    def __init__(self) -> None:
        self.sessions: dict[UUID, ConnectivitySession] = {}
        self.results: dict[UUID, dict[str, object]] = {}
        self.failures: dict[UUID, dict[str, object]] = {}

    def start_inventory(self, session_state: ConnectivitySession) -> None:
        self.sessions[session_state.workflow_id] = session_state

    def get(self, workflow_id: UUID) -> ConnectivitySession | None:
        return self.sessions.get(workflow_id)

    def find_by_chat_message(self, chat_message_id: UUID) -> ConnectivitySession | None:
        return next(
            (
                session
                for session in self.sessions.values()
                if session.chat_message_id == chat_message_id
            ),
            None,
        )

    def record_inventory_result(self, workflow_id: UUID, result: dict[str, object]) -> None:
        self.results[workflow_id] = result

    def record_failure(self, workflow_id: UUID, error: dict[str, object]) -> None:
        self.failures[workflow_id] = error


def handler(protocol: Protocol, model: type[Model]) -> Any:
    return protocol.signed_message_handlers[Model.build_schema_digest(model)]


@pytest.mark.asyncio
async def test_card_submission_reaches_inventory_agent_and_returns_assessment_card() -> None:
    store = FakeWorkflowStore()
    coordinator = FetchConnectivityCoordinator(
        inventory_address="agent1inventory",
        requests=FakeRequestFactory(),
        workflows=store,
    )
    chat_protocol = build_chat_protocol(coordinator)
    inventory_protocol = build_inventory_protocol()
    response_protocol = build_planning_response_protocol(coordinator)

    user_context = FakeContext()
    card_reply = json.dumps(
        {
            "action": "submit_demand",
            "values": {
                "product_sku": "MOTO-URBANX-2026",
                "quantity": 75,
                "required_by": "2026-12-01",
                "priority": "rush",
                "currency": "USD",
            },
        }
    )
    incoming = ChatMessage(
        timestamp=datetime(2026, 10, 16, tzinfo=UTC),
        msg_id=uuid4(),
        content=[TextContent(text=card_reply)],
    )
    await handler(chat_protocol, ChatMessage)(user_context, "agent1manager", incoming)

    assert isinstance(user_context.sent[0][1], ChatAcknowledgement)
    inventory_destination, wire_request = user_context.sent[1]
    assert inventory_destination == "agent1inventory"
    assert isinstance(wire_request, DemandPlanningTransport)
    request = wire_request.to_domain()
    assert request.demand.quantity == Decimal("75")
    assert store.sessions[request.workflow_id].conversation_id == "agent1manager"

    inventory_context = FakeContext()
    await handler(inventory_protocol, DemandPlanningTransport)(
        inventory_context, "agent1orchestrator", wire_request
    )
    response_destination, wire_report = inventory_context.sent[0]
    assert response_destination == "agent1orchestrator"
    assert isinstance(wire_report, InventoryShortageTransport)

    orchestrator_context = FakeContext()
    await handler(response_protocol, InventoryShortageTransport)(
        orchestrator_context, "agent1inventory", wire_report
    )
    chat_destination, chat_response = orchestrator_context.sent[0]
    assert chat_destination == "agent1manager"
    assert isinstance(chat_response, ChatMessage)
    metadata = next(
        item.metadata for item in chat_response.content if isinstance(item, MetadataContent)
    )
    payload = json.loads(metadata["card_payload"])
    assert payload["has_shortages"] is True
    engine = payload["components"][0]
    assert engine["available_to_promise"] == "20"
    assert engine["shortage"] == "55"
    assert store.results[request.workflow_id]["has_shortages"] is True

    duplicate_context = FakeContext()
    await handler(chat_protocol, ChatMessage)(duplicate_context, "agent1manager", incoming)
    assert isinstance(duplicate_context.sent[0][1], ChatAcknowledgement)
    assert isinstance(duplicate_context.sent[1][1], ChatMessage)
    assert "already accepted" in duplicate_context.sent[1][1].content[0].text
    assert len(store.sessions) == 1


def test_transport_revalidates_the_domain_envelope() -> None:
    request = FakeRequestFactory().create_request(
        DemandIntake(
            product_sku="MOTO-URBANX-2026",
            quantity=75,
            required_by=date(2026, 12, 1),
            priority="rush",
        ),
        workflow_id=uuid4(),
        correlation_id=uuid4(),
    )
    wire = DemandPlanningTransport.from_domain(request)
    assert DemandPlanningTransport.parse_raw(wire.json()).to_domain() == request
    tampered = wire.copy(update={"workflow_id": str(uuid4())})
    with pytest.raises(ValueError, match="envelope"):
        tampered.to_domain()


def test_inventory_report_transport_round_trip_is_lossless() -> None:
    request = FakeRequestFactory().create_request(
        DemandIntake(
            product_sku="MOTO-URBANX-2026",
            quantity=75,
            required_by=date(2026, 12, 1),
            priority="rush",
        ),
        workflow_id=uuid4(),
        correlation_id=uuid4(),
    )
    report: InventoryShortageReport = InventoryAgentService().handle(request)
    assert InventoryShortageTransport.from_domain(report).to_domain() == report


@pytest.mark.asyncio
async def test_inventory_protocol_returns_bounded_failure_for_tampered_request() -> None:
    request = FakeRequestFactory().create_request(
        DemandIntake(
            product_sku="MOTO-URBANX-2026",
            quantity=75,
            required_by=date(2026, 12, 1),
            priority="rush",
        ),
        workflow_id=uuid4(),
        correlation_id=uuid4(),
    )
    wire = DemandPlanningTransport.from_domain(request).copy(update={"workflow_id": str(uuid4())})
    context = FakeContext()
    await handler(build_inventory_protocol(), DemandPlanningTransport)(
        context, "agent1orchestrator", wire
    )
    assert isinstance(context.sent[0][1], PlanningFailureTransport)
    failure = context.sent[0][1]
    assert failure.code == "INVALID_PLANNING_REQUEST"
    assert "Traceback" not in failure.message


@pytest.mark.asyncio
async def test_failed_inventory_dispatch_is_persisted_and_reported_without_stack_trace() -> None:
    store = FakeWorkflowStore()
    coordinator = FetchConnectivityCoordinator(
        inventory_address="agent1inventory",
        requests=FakeRequestFactory(),
        workflows=store,
    )
    context = FailedDeliveryContext()
    card_reply = json.dumps(
        {
            "action": "submit_demand",
            "values": {
                "product_sku": "MOTO-URBANX-2026",
                "quantity": 75,
                "required_by": "2026-12-01",
                "priority": "rush",
            },
        }
    )
    incoming = ChatMessage(
        timestamp=datetime(2026, 10, 16, tzinfo=UTC),
        msg_id=uuid4(),
        content=[TextContent(text=card_reply)],
    )
    await handler(build_chat_protocol(coordinator), ChatMessage)(context, "agent1manager", incoming)
    assert len(store.failures) == 1
    error_reply = context.sent[-1][1]
    assert isinstance(error_reply, ChatMessage)
    assert "Inventory Agent could not be reached" in error_reply.content[0].text
    assert "Traceback" not in error_reply.content[0].text


def repository_session_factory() -> tuple[Any, Any]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = (
        Product.__table__,
        Bom.__table__,
        BomItem.__table__,
        DemandOrder.__table__,
        InventoryBalance.__table__,
        InventoryReservation.__table__,
        InboundSupply.__table__,
        WorkflowRun.__table__,
    )
    for table in tables:
        table.create(engine)

    def sessions() -> Session:
        return Session(engine)

    return engine, sessions


def test_planning_and_workflow_repositories_persist_before_transport() -> None:
    engine, sessions = repository_session_factory()
    product_id = uuid4()
    bom_id = uuid4()
    existing_demand_id = uuid4()
    with sessions() as session, session.begin():
        session.add(
            Product(
                id=product_id,
                sku="MOTO-URBANX-2026",
                name="UrbanX Motorcycle",
                version=1,
                unit="EA",
                active=True,
            )
        )
        session.add(
            Bom(
                id=bom_id,
                product_id=product_id,
                version=1,
                effective_from=datetime(2026, 1, 1, tzinfo=UTC),
                status="ACTIVE",
            )
        )
        session.add(
            BomItem(
                bom_id=bom_id,
                component_sku="ENGINE-001",
                quantity_per=Decimal("1"),
                scrap_factor=Decimal("0"),
                unit="EA",
            )
        )
        session.add(
            DemandOrder(
                id=existing_demand_id,
                external_ref="PROTECTED-ORDER",
                product_id=product_id,
                quantity=Decimal("100"),
                required_by=date(2026, 11, 15),
                priority="high",
                status="CONFIRMED",
            )
        )
        session.add(
            InventoryReservation(
                demand_order_id=existing_demand_id,
                component_sku="ENGINE-001",
                quantity=Decimal("100"),
                priority="high",
                status="COMMITTED",
            )
        )
        session.add(
            InventoryBalance(
                component_sku="ENGINE-001",
                location="MAIN",
                on_hand=Decimal("130"),
                safety_stock=Decimal("10"),
                version=1,
            )
        )
        session.add(
            InboundSupply(
                component_sku="ENGINE-001",
                quantity=Decimal("5"),
                expected_at=datetime(2026, 11, 20, tzinfo=UTC),
                confidence=Decimal("1"),
                status="CONFIRMED",
            )
        )

    workflow_id = uuid4()
    correlation_id = uuid4()
    planning_repository = PlanningRequestRepository(sessions)
    request = planning_repository.create_request(
        DemandIntake(
            product_sku="MOTO-URBANX-2026",
            quantity=75,
            required_by=date(2026, 12, 1),
            priority="rush",
        ),
        workflow_id=workflow_id,
        correlation_id=correlation_id,
        as_of=datetime(2026, 10, 16, tzinfo=UTC),
    )
    position = request.inventory_positions[0]
    assert position.on_hand == Decimal("130")
    assert position.confirmed_inbound == Decimal("5")
    assert position.existing_reservations == Decimal("100")

    workflow_repository = WorkflowRepository(sessions)
    chat_message_id = uuid4()
    workflow_repository.start_inventory(
        ConnectivitySession(
            workflow_id=workflow_id,
            conversation_id="agent1manager",
            correlation_id=correlation_id,
            request_message_id=request.message_id,
            chat_message_id=chat_message_id,
            status="INVENTORY_PLANNING",
        )
    )
    duplicate = workflow_repository.find_by_chat_message(chat_message_id)
    assert duplicate is not None
    assert duplicate.workflow_id == workflow_id
    report = InventoryAgentService().handle(request)
    planning_repository.save_tentative_reservations(workflow_id, report.tentative_reservations)
    planning_repository.save_tentative_reservations(workflow_id, report.tentative_reservations)
    workflow_repository.record_inventory_result(
        workflow_id,
        {
            "assessment": report.assessment.model_dump(mode="json"),
            "has_shortages": report.assessment.has_shortages,
        },
    )

    with sessions() as session:
        persisted_demand = session.scalar(
            select(DemandOrder).where(DemandOrder.external_ref == f"ASI-{workflow_id}")
        )
        workflow = session.get(WorkflowRun, workflow_id)
        assert persisted_demand is not None
        assert workflow is not None
        assert workflow.status == "SOURCING"
        assert workflow.checkpoint_version == 2
        reservations = tuple(
            session.scalars(
                select(InventoryReservation).where(
                    InventoryReservation.demand_order_id == workflow_id
                )
            )
        )
        assert len(reservations) == 1
        assert reservations[0].status == "TENTATIVE"

    assert planning_repository.transition_reservations(workflow_id, status="RELEASED") == 1
    assert planning_repository.transition_reservations(workflow_id, status="RELEASED") == 0
    with sessions() as session:
        released = session.scalar(
            select(InventoryReservation).where(InventoryReservation.demand_order_id == workflow_id)
        )
        assert released is not None
        assert released.status == "RELEASED"

    committed_workflow_id = uuid4()
    committed_request = planning_repository.create_request(
        DemandIntake(
            product_sku="MOTO-URBANX-2026",
            quantity=10,
            required_by=date(2026, 12, 1),
            priority="rush",
        ),
        workflow_id=committed_workflow_id,
        correlation_id=uuid4(),
        as_of=datetime(2026, 10, 16, tzinfo=UTC),
    )
    committed_report = InventoryAgentService().handle(committed_request)
    planning_repository.save_tentative_reservations(
        committed_workflow_id, committed_report.tentative_reservations
    )
    assert planning_repository.transition_reservations(
        committed_workflow_id, status="COMMITTED"
    ) == len(committed_report.tentative_reservations)
    with sessions() as session:
        committed = tuple(
            session.scalars(
                select(InventoryReservation).where(
                    InventoryReservation.demand_order_id == committed_workflow_id
                )
            )
        )
        assert committed
        assert {reservation.status for reservation in committed} == {"COMMITTED"}
    engine.dispose()
