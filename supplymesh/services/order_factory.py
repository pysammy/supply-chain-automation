from collections import defaultdict
from decimal import Decimal
from uuid import UUID, uuid5

from supplymesh.domain.procurement import PurchaseOrder, PurchaseOrderLine
from supplymesh.domain.sourcing import PlanAllocation, ProcurementPlan


def purchase_orders_from_plan(
    workflow_id: UUID, plan: ProcurementPlan
) -> tuple[PurchaseOrder, ...]:
    """Convert a selected optimizer result into stable, supplier-grouped purchase orders."""
    by_supplier: dict[str, list[PlanAllocation]] = defaultdict(list)
    for allocation in plan.allocations:
        by_supplier[allocation.supplier_id].append(allocation)

    purchase_orders: list[PurchaseOrder] = []
    for supplier_index, supplier_id in enumerate(sorted(by_supplier), start=1):
        allocations = sorted(
            by_supplier[supplier_id],
            key=lambda item: (item.component_sku, item.quote_ref),
        )
        # Our supported 850 subset carries one requested date at PO level. Use the
        # latest allocation date so every line is representable without promising
        # any component earlier than the selected sourcing plan can deliver it.
        requested_date = max(allocation.promised_date for allocation in allocations)
        lines = tuple(
            PurchaseOrderLine(
                line_number=index,
                component_sku=allocation.component_sku,
                quantity=Decimal(allocation.quantity),
                unit_price=Decimal(allocation.unit_price_minor) / 100,
                requested_date=requested_date,
            )
            for index, allocation in enumerate(allocations, start=1)
        )
        po_id = uuid5(workflow_id, f"{plan.plan_id}:{supplier_id}:v1")
        purchase_orders.append(
            PurchaseOrder(
                po_id=po_id,
                po_number=f"SM-{workflow_id.hex[:10].upper()}-{supplier_index:02d}",
                workflow_id=workflow_id,
                supplier_id=supplier_id,
                version=1,
                currency=plan.currency,
                lines=lines,
                total=sum((line.quantity * line.unit_price for line in lines), Decimal("0")),
            )
        )
    return tuple(purchase_orders)
