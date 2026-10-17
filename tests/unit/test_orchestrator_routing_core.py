from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from supplymesh.domain.sourcing import PlanAllocation, ProcurementPlan
from supplymesh.services.order_factory import purchase_orders_from_plan
from supplymesh.workflows.procurement_graph import build_event_transition_graph


def test_event_graph_rejects_out_of_order_specialist_result() -> None:
    graph = build_event_transition_graph()
    with pytest.raises(ValueError, match="invalid while status"):
        graph.invoke(
            {
                "status": "INVENTORY_PLANNING",
                "event": {"kind": "plans_ready"},
            }
        )


def test_event_graph_advances_specialist_and_approval_stages() -> None:
    graph = build_event_transition_graph()
    state = graph.invoke({"status": "INVENTORY_PLANNING", "event": {"kind": "inventory_shortage"}})
    assert state["status"] == "SOURCING"
    state = graph.invoke({**state, "event": {"kind": "plans_ready"}})
    assert state["status"] == "WAITING_FOR_PLAN_APPROVAL"
    state = graph.invoke({**state, "event": {"kind": "plan_approved"}})
    assert state["status"] == "WAITING_FOR_PO_APPROVAL"
    state = graph.invoke({**state, "event": {"kind": "purchase_orders_approved"}})
    assert state["status"] == "PO_EXECUTION"


def test_plan_to_purchase_orders_is_typed_deterministic_and_supplier_grouped() -> None:
    workflow_id = uuid4()
    plan = ProcurementPlan(
        plan_id="split-test",
        name="split",
        allocations=(
            PlanAllocation(
                component_sku="B",
                supplier_id="supplier-beta",
                quote_ref="q2",
                quantity=2,
                unit_price_minor=250,
                promised_date=date(2026, 12, 2),
            ),
            PlanAllocation(
                component_sku="A",
                supplier_id="supplier-alpha",
                quote_ref="q1",
                quantity=3,
                unit_price_minor=125,
                promised_date=date(2026, 12, 1),
            ),
        ),
        total_cost_minor=875,
        currency="USD",
        latest_arrival=date(2026, 12, 2),
        total_late_days=0,
        risk_score_basis_points=100,
        supplier_count=2,
    )

    first = purchase_orders_from_plan(workflow_id, plan)
    second = purchase_orders_from_plan(workflow_id, plan)

    assert first == second
    assert [po.supplier_id for po in first] == ["supplier-alpha", "supplier-beta"]
    assert sum((po.total for po in first), Decimal("0")) == Decimal("8.75")


def test_supplier_po_uses_latest_line_date_supported_by_850_subset() -> None:
    workflow_id = uuid4()
    plan = ProcurementPlan(
        plan_id="multi-line-test",
        name="split",
        allocations=(
            PlanAllocation(
                component_sku="A",
                supplier_id="supplier-alpha",
                quote_ref="q1",
                quantity=3,
                unit_price_minor=125,
                promised_date=date(2026, 12, 1),
            ),
            PlanAllocation(
                component_sku="B",
                supplier_id="supplier-alpha",
                quote_ref="q2",
                quantity=2,
                unit_price_minor=250,
                promised_date=date(2026, 12, 3),
            ),
        ),
        total_cost_minor=875,
        currency="USD",
        latest_arrival=date(2026, 12, 3),
        total_late_days=0,
        risk_score_basis_points=100,
        supplier_count=1,
    )

    purchase_order = purchase_orders_from_plan(workflow_id, plan)[0]

    assert {line.requested_date for line in purchase_order.lines} == {date(2026, 12, 3)}
