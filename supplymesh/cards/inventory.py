from supplymesh.cards.renderer import CardEnvelope, render_card
from supplymesh.domain.models import InventoryAssessment


def inventory_assessment_detail(assessment: InventoryAssessment) -> CardEnvelope:
    return render_card(
        "detail",
        {
            "title": "Inventory assessment",
            "summary_rows": [
                {"label": "Product", "value": assessment.product_sku},
                {"label": "Demand quantity", "value": str(assessment.demand_quantity)},
                {"label": "Required by", "value": assessment.required_by.isoformat()},
                {"label": "Shortages", "value": "Yes" if assessment.has_shortages else "No"},
                *[
                    {
                        "label": line.component_sku,
                        "value": (
                            f"Required {line.gross_required}; ATP {line.available_to_promise}; "
                            f"reserved {line.tentative_reservation}; shortage {line.shortage}. "
                        )
                        + "; ".join(line.evidence),
                    }
                    for line in assessment.lines
                ],
            ],
        },
        informational=True,
    )
