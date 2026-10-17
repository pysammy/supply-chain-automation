from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from supplymesh.agents.inventory import InventoryAgentService
from supplymesh.domain.enums import Priority
from supplymesh.domain.models import BomComponent, Demand, InventoryPosition
from supplymesh.protocols.planning import DemandPlanningRequest
from supplymesh.services.bom_service import gross_requirement
from supplymesh.services.inventory_service import assess_inventory, available_to_promise


def component(sku: str = "ENGINE-001", scrap: str = "0") -> BomComponent:
    return BomComponent(
        component_sku=sku, quantity_per=Decimal("1"), scrap_factor=Decimal(scrap), unit="EA"
    )


def rush_demand() -> Demand:
    return Demand(
        demand_order_id=uuid4(),
        product_sku="MOTO-URBANX-2026",
        quantity=Decimal("75"),
        required_by=date(2026, 12, 1),
        priority=Priority.RUSH,
    )


def test_scrap_factor_uses_decimal_math() -> None:
    assert gross_requirement(Decimal("75"), component(scrap="0.02")) == Decimal("76.50")


def test_available_to_promise_protects_existing_reservations_and_safety_stock() -> None:
    position = InventoryPosition(
        component_sku="ENGINE-001",
        on_hand=Decimal("130"),
        confirmed_inbound=Decimal("5"),
        existing_reservations=Decimal("100"),
        safety_stock=Decimal("10"),
        unit="EA",
    )
    assert available_to_promise(position) == Decimal("25")


def test_shortage_and_tentative_reservation_do_not_consume_protected_stock() -> None:
    assessment = assess_inventory(
        rush_demand(),
        (component(),),
        (
            InventoryPosition(
                component_sku="ENGINE-001",
                on_hand=Decimal("130"),
                existing_reservations=Decimal("100"),
                safety_stock=Decimal("10"),
                unit="EA",
            ),
        ),
        calculated_at=datetime(2026, 10, 16, tzinfo=UTC),
    )
    line = assessment.lines[0]
    assert line.gross_required == Decimal("75")
    assert line.available_to_promise == Decimal("20")
    assert line.tentative_reservation == Decimal("20")
    assert line.shortage == Decimal("55")


def test_inventory_agent_returns_typed_correlated_report() -> None:
    request = DemandPlanningRequest(
        correlation_id=uuid4(),
        workflow_id=uuid4(),
        sender_agent="orchestrator",
        demand=rush_demand(),
        bom=(component(),),
        inventory_positions=(
            InventoryPosition(
                component_sku="ENGINE-001",
                on_hand=Decimal("0"),
                unit="EA",
            ),
        ),
    )
    report = InventoryAgentService().handle(request)
    assert report.correlation_id == request.correlation_id
    assert report.reply_to == str(request.message_id)
    assert report.assessment.has_shortages
    assert report.assessment.lines[0].shortage == Decimal("75")
