from collections import defaultdict
from datetime import date
from hashlib import sha256

from ortools.sat.python import cp_model

from supplymesh.domain.sourcing import (
    NormalizedQuoteLine,
    OptimizationResult,
    PlanAllocation,
    PlanName,
    ProcurementPlan,
)


def optimize_plans(
    shortages: dict[str, int],
    quote_lines: tuple[NormalizedQuoteLine, ...],
    *,
    currency: str = "USD",
) -> OptimizationResult:
    if not shortages:
        return OptimizationResult(plans=(), infeasibility_reasons=("No shortages to source.",))
    missing = sorted(
        sku
        for sku, quantity in shortages.items()
        if quantity > 0
        and sum(line.offered_quantity for line in quote_lines if line.component_sku == sku)
        < quantity
    )
    if missing:
        return OptimizationResult(
            plans=(),
            infeasibility_reasons=tuple(
                f"Insufficient offered capacity for {sku}: need {shortages[sku]}."
                for sku in missing
            ),
        )

    plans: list[ProcurementPlan] = []
    for name in ("cheapest", "fastest", "balanced", "risk_aware", "split"):
        plan = _solve(name, shortages, quote_lines, currency)
        if plan is not None:
            plans.append(plan)
    if not plans:
        return OptimizationResult(
            plans=(),
            infeasibility_reasons=(
                "Offers have enough nominal capacity but violate minimum-order or "
                "packaging constraints.",
            ),
        )
    return OptimizationResult(plans=tuple(plans))


def _solve(
    name: PlanName,
    shortages: dict[str, int],
    lines: tuple[NormalizedQuoteLine, ...],
    currency: str,
) -> ProcurementPlan | None:
    model = cp_model.CpModel()
    quantities: dict[int, cp_model.IntVar] = {}
    used: dict[int, cp_model.IntVar] = {}
    candidates: dict[str, list[int]] = defaultdict(list)

    for index, line in enumerate(lines):
        if line.component_sku not in shortages or line.offered_quantity <= 0:
            continue
        pack_count = line.offered_quantity // line.packaging_multiple
        packs = model.new_int_var(0, pack_count, f"packs_{index}")
        quantity = model.new_int_var(0, line.offered_quantity, f"quantity_{index}")
        is_used = model.new_bool_var(f"used_{index}")
        model.add(quantity == packs * line.packaging_multiple)
        model.add(quantity == 0).only_enforce_if(is_used.Not())
        model.add(quantity >= line.minimum_order_quantity).only_enforce_if(is_used)
        model.add(quantity <= line.offered_quantity * is_used)
        quantities[index] = quantity
        used[index] = is_used
        candidates[line.component_sku].append(index)

    for sku, shortage in shortages.items():
        indices = candidates.get(sku, [])
        if not indices:
            return None
        model.add(sum(quantities[index] for index in indices) >= shortage)
        if name != "split":
            model.add(sum(used[index] for index in indices) == 1)

    total_cost = sum(quantities[i] * lines[i].unit_price_minor for i in quantities)
    supplier_flags: dict[str, cp_model.IntVar] = {}
    for supplier in sorted({lines[i].supplier_id for i in quantities}):
        flag = model.new_bool_var(f"supplier_{supplier}")
        supplier_flags[supplier] = flag
        supplier_used = [used[i] for i in used if lines[i].supplier_id == supplier]
        model.add_max_equality(flag, supplier_used)
    supplier_count = sum(supplier_flags.values())
    risk = sum(quantities[i] * (10_000 - lines[i].reliability_basis_points) for i in quantities)
    late = sum(
        quantities[i] * max(0, (lines[i].promised_date - lines[i].required_by).days)
        for i in quantities
    )
    arrivals = []
    for index in quantities:
        arrival = model.new_int_var(0, date.max.toordinal(), f"arrival_{index}")
        model.add(arrival == lines[index].promised_date.toordinal()).only_enforce_if(used[index])
        model.add(arrival == 0).only_enforce_if(used[index].Not())
        arrivals.append(arrival)
    latest = model.new_int_var(0, date.max.toordinal(), "latest")
    model.add_max_equality(latest, arrivals)

    if name == "cheapest":
        model.minimize(total_cost)
    elif name == "fastest":
        model.minimize(latest * 1_000_000 + late)
    elif name == "balanced":
        model.minimize(total_cost + late * 100_000 + risk * 10 + supplier_count * 50_000)
    elif name == "risk_aware":
        model.minimize(risk * 1_000 + late * 100_000 + total_cost)
    else:
        model.minimize(total_cost + supplier_count * 10_000)

    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    allocations = tuple(
        PlanAllocation(
            component_sku=lines[i].component_sku,
            supplier_id=lines[i].supplier_id,
            quote_ref=lines[i].quote_ref,
            quantity=solver.value(quantities[i]),
            unit_price_minor=lines[i].unit_price_minor,
            promised_date=lines[i].promised_date,
        )
        for i in sorted(quantities)
        if solver.value(quantities[i]) > 0
    )
    total_quantity = sum(allocation.quantity for allocation in allocations)
    weighted_risk = sum(
        allocation.quantity
        * (
            10_000
            - next(
                line.reliability_basis_points
                for line in lines
                if line.quote_ref == allocation.quote_ref
                and line.component_sku == allocation.component_sku
            )
        )
        for allocation in allocations
    )
    material = "|".join(
        f"{item.component_sku}:{item.supplier_id}:{item.quantity}:{item.unit_price_minor}"
        for item in allocations
    )
    latest_date = max(item.promised_date for item in allocations)
    late_days = sum(
        max(
            0,
            (
                item.promised_date
                - next(
                    line.required_by
                    for line in lines
                    if line.quote_ref == item.quote_ref and line.component_sku == item.component_sku
                )
            ).days,
        )
        for item in allocations
    )
    return ProcurementPlan(
        plan_id=f"{name}-{sha256(material.encode()).hexdigest()[:12]}",
        name=name,
        allocations=allocations,
        total_cost_minor=sum(item.quantity * item.unit_price_minor for item in allocations),
        currency=currency,
        latest_arrival=latest_date,
        total_late_days=late_days,
        risk_score_basis_points=weighted_risk // total_quantity,
        supplier_count=len({item.supplier_id for item in allocations}),
        explanation=(
            f"Objective {name} solved deterministically with OR-Tools CP-SAT.",
            "All minimum-order, packaging, capacity, and shortage constraints were enforced.",
        ),
    )
