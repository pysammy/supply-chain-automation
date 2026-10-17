from supplymesh.cards.renderer import CardEnvelope, render_card
from supplymesh.domain.exceptions import AcknowledgmentComparison


def exception_review(
    comparison: AcknowledgmentComparison, *, po_id: str, po_version: int
) -> CardEnvelope:
    return render_card(
        "review",
        {
            "title": "Supplier acknowledgment exception",
            "target": {"id": po_id, "version": po_version},
            "status": comparison.status,
            "recommended_action": comparison.recommended_action,
            "impact": {
                "cost_delta": str(comparison.absolute_cost_delta),
                "cost_delta_percent": str(comparison.percentage_cost_delta),
                "unfulfilled_quantity": str(comparison.unfulfilled_quantity),
                "production_units_affected": str(comparison.production_units_affected),
                "schedule_delay_days": comparison.estimated_schedule_delay_days,
            },
            "discrepancies": [item.model_dump(mode="json") for item in comparison.discrepancies],
            "actions": [
                {
                    "label": action.replace("_", " ").title(),
                    "value": {
                        "action": action,
                        "target_id": po_id,
                        "target_version": po_version,
                    },
                }
                for action in comparison.allowed_actions
            ],
        },
    )
