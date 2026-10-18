import json
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from uuid import uuid4

from apps.supplier_simulator.config import load_supplier_profiles
from apps.supplier_simulator.service import RfqService
from supplymesh.domain.enums import Priority
from supplymesh.domain.models import BomComponent, Demand, InventoryPosition
from supplymesh.domain.sourcing import RfqLine, RfqRequest
from supplymesh.services.inventory_service import assess_inventory
from supplymesh.services.optimizer import optimize_plans
from supplymesh.services.sourcing_service import normalize_quotes

ROOT = Path(__file__).resolve().parents[2]


def test_documented_demo_shortages_have_supplier_capacity() -> None:
    bom_data = json.loads((ROOT / "data/motorcycle_bom.json").read_text(encoding="utf-8"))
    inventory_data = json.loads((ROOT / "data/initial_inventory.json").read_text(encoding="utf-8"))
    order_data = json.loads((ROOT / "data/existing_orders.json").read_text(encoding="utf-8"))
    reservations: dict[str, Decimal] = {}
    for item in order_data["reservations"]:
        sku = item["component_sku"]
        reservations[sku] = reservations.get(sku, Decimal("0")) + Decimal(item["quantity"])

    components = tuple(BomComponent.model_validate(item) for item in bom_data["components"])
    positions = tuple(
        InventoryPosition(
            component_sku=item["component_sku"],
            on_hand=Decimal(item["on_hand"]),
            existing_reservations=reservations.get(item["component_sku"], Decimal("0")),
            safety_stock=Decimal(item["safety_stock"]),
            unit="EA",
        )
        for item in inventory_data["balances"]
    )
    assessment = assess_inventory(
        Demand(
            demand_order_id=uuid4(),
            product_sku=bom_data["product"]["sku"],
            quantity=Decimal("75"),
            required_by=date(2026, 12, 1),
            priority=Priority.RUSH,
        ),
        components,
        positions,
    )
    profiles = load_supplier_profiles()

    shortage_skus = {line.component_sku for line in assessment.lines if line.shortage > 0}
    assert 3 <= len(shortage_skus) <= 5
    for line in assessment.lines:
        if line.shortage <= 0:
            continue
        required = int(line.shortage.to_integral_value(rounding=ROUND_CEILING))
        offered = sum(
            int(profile.catalog[line.component_sku].available_capacity)
            for profile in profiles.values()
            if line.component_sku in profile.catalog
        )
        assert offered >= required, line.component_sku

    shortages = {
        line.component_sku: int(line.shortage.to_integral_value(rounding=ROUND_CEILING))
        for line in assessment.lines
        if line.shortage > 0
    }
    rfq = RfqRequest(
        rfq_id=uuid4(),
        currency="USD",
        valid_until=datetime.now(UTC) + timedelta(hours=1),
        lines=tuple(
            RfqLine(
                line_number=i, component_sku=sku, quantity=quantity, required_by=date(2026, 12, 1)
            )
            for i, (sku, quantity) in enumerate(shortages.items(), start=1)
        ),
    )
    service = RfqService(profiles)
    quotes = tuple(
        service.quote(supplier, rfq, f"demo-{supplier}", today=date(2026, 10, 17))
        for supplier in profiles
    )
    result = optimize_plans(
        shortages,
        normalize_quotes(
            quotes,
            dict.fromkeys(shortages, "2026-12-01"),
            {"USD": Decimal("1"), "EUR": Decimal("1.10")},
        ),
    )
    assert {plan.name for plan in result.plans} == {
        "cheapest",
        "fastest",
        "balanced",
        "risk_aware",
        "split",
    }
    for plan in result.plans:
        for sku, required in shortages.items():
            assert sum(a.quantity for a in plan.allocations if a.component_sku == sku) >= required
