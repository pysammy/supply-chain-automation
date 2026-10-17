from decimal import Decimal

from supplymesh.domain.models import BomComponent


def gross_requirement(demand_quantity: Decimal, component: BomComponent) -> Decimal:
    if demand_quantity <= 0:
        raise ValueError("demand quantity must be positive")
    base = demand_quantity * component.quantity_per
    return base * (Decimal("1") + component.scrap_factor)


def expand_bom(
    demand_quantity: Decimal, components: tuple[BomComponent, ...]
) -> dict[str, Decimal]:
    if not components:
        raise ValueError("effective BOM must contain at least one component")
    requirements: dict[str, Decimal] = {}
    for component in components:
        requirements[component.component_sku] = requirements.get(
            component.component_sku, Decimal("0")
        ) + gross_requirement(demand_quantity, component)
    return requirements
