from datetime import date

from supplymesh.domain.sourcing import NormalizedQuoteLine
from supplymesh.services.optimizer import optimize_plans


def line(
    supplier: str,
    price: int,
    days: int,
    reliability: int,
    capacity: int = 100,
    pack: int = 5,
) -> NormalizedQuoteLine:
    return NormalizedQuoteLine(
        supplier_id=supplier,
        quote_ref=f"Q-{supplier}",
        component_sku="ENGINE-001",
        offered_quantity=capacity,
        unit_price_minor=price,
        promised_date=date(2026, 11, days),
        required_by=date(2026, 11, 20),
        reliability_basis_points=reliability,
        minimum_order_quantity=5,
        packaging_multiple=pack,
    )


def test_optimizer_returns_all_named_deterministic_plans() -> None:
    result = optimize_plans(
        {"ENGINE-001": 75},
        (
            line("alpha", 82_000, 18, 9400),
            line("beta", 87_500, 10, 9800),
            line("gamma", 79_000, 25, 8800, capacity=50, pack=10),
        ),
    )
    assert {plan.name for plan in result.plans} == {
        "cheapest",
        "fastest",
        "balanced",
        "risk_aware",
        "split",
    }
    assert all(sum(item.quantity for item in plan.allocations) >= 75 for plan in result.plans)
    assert (
        next(plan for plan in result.plans if plan.name == "cheapest").allocations[0].supplier_id
        == "alpha"
    )
    assert (
        next(plan for plan in result.plans if plan.name == "fastest").allocations[0].supplier_id
        == "beta"
    )


def test_optimizer_explains_insufficient_capacity_without_fabricating_plan() -> None:
    result = optimize_plans({"ENGINE-001": 75}, (line("alpha", 1, 18, 9400, capacity=50),))
    assert result.plans == ()
    assert "Insufficient offered capacity" in result.infeasibility_reasons[0]
