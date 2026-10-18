from supplymesh.cards.renderer import CardEnvelope, render_card
from supplymesh.domain.exceptions import AcknowledgmentComparison
from supplymesh.domain.procurement import PurchaseOrder


def exception_review(comparison: AcknowledgmentComparison, *, po: PurchaseOrder) -> CardEnvelope:
    components = {line.line_number: line.component_sku for line in po.lines}
    discrepancy_nodes = []
    for item in comparison.discrepancies:
        label = "Order-level"
        if item.po_line_number is not None:
            sku = components.get(item.po_line_number, "Unknown component")
            label = f"{sku} (PO line {item.po_line_number})"
        discrepancy_nodes.append(
            {
                "type": "text",
                "value": (
                    f"{label}: {item.category} ({item.severity}); "
                    f"expected {item.expected_value}, acknowledged {item.actual_value}."
                ),
            }
        )
    return render_card(
        "custom",
        {
            "root": {
                "type": "section",
                "title": "Supplier acknowledgment exception",
                "children": [
                    {
                        "type": "text",
                        "value": (
                            f"Supplier: {po.supplier_id}. "
                            f"Purchase order: {po.po_number}, version {po.version}."
                        ),
                    },
                    {
                        "type": "text",
                        "value": (
                            f"Status: {comparison.status}. "
                            f"Recommended action: {comparison.recommended_action}."
                        ),
                    },
                    {
                        "type": "text",
                        "value": (
                            f"Cost change: {po.currency} {comparison.absolute_cost_delta:.2f} "
                            f"({comparison.percentage_cost_delta:.2f}%). "
                            f"Unfulfilled quantity: {comparison.unfulfilled_quantity}. "
                            f"Production units affected: {comparison.production_units_affected}. "
                            f"Schedule delay: {comparison.estimated_schedule_delay_days} days."
                        ),
                    },
                    *discrepancy_nodes,
                    *[
                        {
                            "type": "button",
                            "label": action.title(),
                            "primary": action == "counter",
                            "action": {
                                "selection": {
                                    "action": action,
                                    "target_id": str(po.po_id),
                                    "target_version": po.version,
                                }
                            },
                        }
                        for action in comparison.allowed_actions
                        if action not in {"switch", "split"}
                    ],
                ],
            }
        },
    )
