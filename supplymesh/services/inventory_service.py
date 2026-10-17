from datetime import UTC, datetime
from decimal import Decimal

from supplymesh.domain.models import (
    BomComponent,
    Demand,
    InventoryAssessment,
    InventoryPosition,
    ShortageLine,
    TentativeReservation,
)
from supplymesh.services.bom_service import expand_bom


def available_to_promise(position: InventoryPosition) -> Decimal:
    return (
        position.on_hand
        + position.confirmed_inbound
        - position.existing_reservations
        - position.safety_stock
    )


def assess_inventory(
    demand: Demand,
    components: tuple[BomComponent, ...],
    positions: tuple[InventoryPosition, ...],
    *,
    calculated_at: datetime | None = None,
) -> InventoryAssessment:
    position_by_sku = {position.component_sku: position for position in positions}
    requirements = expand_bom(demand.quantity, components)
    lines: list[ShortageLine] = []

    for component in components:
        sku = component.component_sku
        if any(line.component_sku == sku for line in lines):
            continue
        position = position_by_sku.get(
            sku,
            InventoryPosition(component_sku=sku, on_hand=Decimal("0"), unit=component.unit),
        )
        atp = available_to_promise(position)
        usable = max(atp, Decimal("0"))
        gross = requirements[sku]
        reserved = min(gross, usable)
        shortage = max(Decimal("0"), gross - usable)
        lines.append(
            ShortageLine(
                component_sku=sku,
                unit=component.unit,
                gross_required=gross,
                available_to_promise=atp,
                tentative_reservation=reserved,
                shortage=shortage,
                evidence=(
                    f"on_hand={position.on_hand}",
                    f"confirmed_inbound={position.confirmed_inbound}",
                    f"existing_reservations={position.existing_reservations}",
                    f"safety_stock={position.safety_stock}",
                ),
            )
        )

    return InventoryAssessment(
        demand_order_id=demand.demand_order_id,
        product_sku=demand.product_sku,
        demand_quantity=demand.quantity,
        required_by=demand.required_by,
        lines=tuple(lines),
        has_shortages=any(line.shortage > 0 for line in lines),
        calculated_at=calculated_at or datetime.now(UTC),
    )


def tentative_reservations(
    demand: Demand, assessment: InventoryAssessment
) -> tuple[TentativeReservation, ...]:
    return tuple(
        TentativeReservation(
            demand_order_id=demand.demand_order_id,
            component_sku=line.component_sku,
            quantity=line.tentative_reservation,
            priority=demand.priority,
        )
        for line in assessment.lines
        if line.tentative_reservation > 0
    )
