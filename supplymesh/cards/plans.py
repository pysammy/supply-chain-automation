from decimal import Decimal

from supplymesh.cards.renderer import CardEnvelope, render_card
from supplymesh.domain.sourcing import ProcurementPlan


def procurement_plan_carousel(plans: tuple[ProcurementPlan, ...]) -> CardEnvelope:
    return render_card(
        "carousel",
        {
            "title": "Procurement plans",
            "items": [
                {
                    "id": plan.plan_id,
                    "title": plan.name.replace("_", " ").title(),
                    "subtitle": (
                        f"Arrives {plan.latest_arrival}; {plan.supplier_count} supplier(s); "
                        f"{plan.total_late_days} late days; "
                        f"risk {plan.risk_score_basis_points}/10000"
                    ),
                    "secondary_text": f"{plan.currency} {Decimal(plan.total_cost_minor) / 100:.2f}",
                    "primary_cta": {
                        "label": "Review plan",
                        "selection": {"action": "review_plan", "target_id": plan.plan_id},
                    },
                }
                for plan in plans
            ],
        },
    )


def plan_approval_review(plan: ProcurementPlan, version: int) -> CardEnvelope:
    target = {"target_id": plan.plan_id, "target_version": version}
    return render_card(
        "review",
        {
            "title": f"Approve {plan.name.replace('_', ' ')} plan",
            "summary_rows": [
                {
                    "label": "Total",
                    "value": f"{plan.currency} {Decimal(plan.total_cost_minor) / 100:.2f}",
                },
                {"label": "Latest arrival", "value": plan.latest_arrival.isoformat()},
                *[
                    {
                        "label": item.component_sku,
                        "value": (
                            f"{item.quantity} from {item.supplier_id} at {plan.currency} "
                            f"{Decimal(item.unit_price_minor) / 100:.2f}, "
                            f"arriving {item.promised_date}"
                        ),
                    }
                    for item in plan.allocations
                ],
            ],
            "approve_cta": {
                "label": "Approve",
                "primary": True,
                "selection": {"action": "approve_plan", **target},
            },
            "reject_cta": {"label": "Reject", "selection": {"action": "reject_plan", **target}},
        },
    )
