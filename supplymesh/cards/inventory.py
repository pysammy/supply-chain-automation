from decimal import Decimal

from supplymesh.cards.renderer import CardEnvelope, render_card
from supplymesh.domain.models import InventoryAssessment


def _quantity(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def inventory_assessment_detail(
    assessment: InventoryAssessment,
    *,
    workflow_id: str | None = None,
    workflow_version: int | None = None,
) -> CardEnvelope:
    ctas = (
        [
            {
                "label": "Continue to sourcing" if assessment.has_shortages else "View completion",
                "selection": {
                    "action": "continue_to_sourcing"
                    if assessment.has_shortages
                    else "show_completion",
                    "target_id": workflow_id,
                    "target_version": workflow_version,
                },
                "primary": True,
            }
        ]
        if workflow_id is not None
        else []
    )
    return render_card(
        "detail",
        {
            "title": "Inventory assessment",
            "ctas": ctas,
            "summary_rows": [
                {"label": "Product", "value": assessment.product_sku},
                {"label": "Demand quantity", "value": _quantity(assessment.demand_quantity)},
                {"label": "Required by", "value": assessment.required_by.isoformat()},
                {"label": "Shortages", "value": "Yes" if assessment.has_shortages else "No"},
                *[
                    {
                        "label": line.component_sku,
                        "value": (
                            f"Required {_quantity(line.gross_required)}; "
                            f"available {_quantity(line.available_to_promise)}; "
                            f"shortage {_quantity(line.shortage)} {line.unit}."
                        ),
                    }
                    for line in assessment.lines
                ],
            ],
        },
        informational=workflow_id is None,
    )
