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
                    "summary": {
                        "total_cost_minor": plan.total_cost_minor,
                        "currency": plan.currency,
                        "latest_arrival": plan.latest_arrival.isoformat(),
                        "supplier_count": plan.supplier_count,
                        "risk_score_basis_points": plan.risk_score_basis_points,
                        "late_days": plan.total_late_days,
                    },
                    "action": {
                        "label": "Review plan",
                        "value": {"action": "review_plan", "target_id": plan.plan_id},
                    },
                }
                for plan in plans
            ],
        },
    )


def plan_approval_review(plan: ProcurementPlan, version: int) -> CardEnvelope:
    return render_card(
        "review",
        {
            "title": f"Approve {plan.name.replace('_', ' ')} plan",
            "target": {"id": plan.plan_id, "version": version},
            "total_cost_minor": plan.total_cost_minor,
            "currency": plan.currency,
            "latest_arrival": plan.latest_arrival.isoformat(),
            "allocations": [item.model_dump(mode="json") for item in plan.allocations],
            "actions": [
                {
                    "label": "Approve",
                    "style": "primary",
                    "value": {
                        "action": "approve_plan",
                        "target_id": plan.plan_id,
                        "target_version": version,
                    },
                },
                {"label": "Modify", "value": {"action": "modify_plan", "target_id": plan.plan_id}},
                {"label": "Reject", "value": {"action": "reject_plan", "target_id": plan.plan_id}},
            ],
        },
    )
