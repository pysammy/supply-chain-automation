import json
from datetime import date

from supplymesh.cards.plans import plan_approval_review, procurement_plan_carousel
from supplymesh.domain.sourcing import PlanAllocation, ProcurementPlan


def sample_plan() -> ProcurementPlan:
    return ProcurementPlan(
        plan_id="balanced-1",
        name="balanced",
        allocations=(
            PlanAllocation(
                component_sku="ENGINE-001",
                supplier_id="supplier-alpha",
                quote_ref="Q-1",
                quantity=75,
                unit_price_minor=82_000,
                promised_date=date(2026, 11, 20),
            ),
        ),
        total_cost_minor=6_150_000,
        currency="USD",
        latest_arrival=date(2026, 11, 20),
        total_late_days=0,
        risk_score_basis_points=600,
        supplier_count=1,
    )


def test_plan_cards_preserve_target_and_version() -> None:
    plan = sample_plan()
    carousel = json.loads(procurement_plan_carousel((plan,)).card_payload)
    review = json.loads(plan_approval_review(plan, 3).card_payload)
    assert carousel["items"][0]["id"] == plan.plan_id
    assert review["target"] == {"id": plan.plan_id, "version": 3}
    assert review["actions"][0]["value"]["target_version"] == 3
