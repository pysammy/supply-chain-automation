from supplymesh.cards.renderer import CardEnvelope, render_card
from supplymesh.domain.models import InventoryAssessment


def inventory_assessment_detail(assessment: InventoryAssessment) -> CardEnvelope:
    return render_card(
        "custom",
        {
            "title": "Inventory assessment",
            "demand_order_id": str(assessment.demand_order_id),
            "product_sku": assessment.product_sku,
            "demand_quantity": str(assessment.demand_quantity),
            "required_by": assessment.required_by.isoformat(),
            "has_shortages": assessment.has_shortages,
            "components": [line.model_dump(mode="json") for line in assessment.lines],
        },
    )
