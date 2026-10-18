from supplymesh.cards.renderer import CardEnvelope, render_card
from supplymesh.domain.exceptions import AcknowledgmentComparison


def exception_review(
    comparison: AcknowledgmentComparison, *, po_id: str, po_version: int
) -> CardEnvelope:
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
                            f"Status: {comparison.status}. "
                            f"Recommended action: {comparison.recommended_action}."
                        ),
                    },
                    {
                        "type": "text",
                        "value": (
                            f"Cost change: {comparison.absolute_cost_delta} "
                            f"({comparison.percentage_cost_delta}%). "
                            f"Unfulfilled quantity: {comparison.unfulfilled_quantity}. "
                            f"Production units affected: {comparison.production_units_affected}. "
                            f"Schedule delay: {comparison.estimated_schedule_delay_days} days."
                        ),
                    },
                    *[
                        {
                            "type": "text",
                            "value": (
                                f"{item.category} ({item.severity}): "
                                f"expected {item.expected_value}, "
                                f"acknowledged {item.actual_value}."
                            ),
                        }
                        for item in comparison.discrepancies
                    ],
                    *[
                        {
                            "type": "button",
                            "label": action.title(),
                            "primary": action == "counter",
                            "action": {
                                "selection": {
                                    "action": action,
                                    "target_id": po_id,
                                    "target_version": po_version,
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
