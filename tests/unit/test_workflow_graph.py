from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from supplymesh.workflows.procurement_graph import build_procurement_graph


def config(thread_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": thread_id}}


def test_no_shortage_reaches_completed_without_external_actions() -> None:
    graph = build_procurement_graph(InMemorySaver())
    result = graph.invoke(
        {"workflow_id": str(uuid4()), "has_shortages": False}, config("no-shortage")
    )
    assert result["status"] == "COMPLETED"
    assert "__interrupt__" not in result


def test_happy_path_requires_separate_plan_and_po_approvals() -> None:
    graph = build_procurement_graph(InMemorySaver())
    state = {
        "workflow_id": str(uuid4()),
        "has_shortages": True,
        "selected_plan_id": "plan-1",
        "selected_plan_version": 2,
        "purchase_order_batch_id": "batch-1",
        "purchase_order_batch_version": 1,
        "ack_has_conflict": False,
    }
    cfg = config("happy")
    waiting_plan = graph.invoke(state, cfg)
    assert waiting_plan["status"] == "WAITING_FOR_PLAN_APPROVAL"
    waiting_po = graph.invoke(
        Command(resume={"action": "approve_plan", "target_id": "plan-1", "target_version": 2}),
        cfg,
    )
    assert waiting_po["status"] == "WAITING_FOR_PO_APPROVAL"
    completed = graph.invoke(
        Command(
            resume={
                "action": "approve_and_send_purchase_orders",
                "target_id": "batch-1",
                "target_version": 1,
            }
        ),
        cfg,
    )
    assert completed["status"] == "COMPLETED"


def test_stale_plan_approval_is_rejected() -> None:
    graph = build_procurement_graph(InMemorySaver())
    cfg = config("stale")
    graph.invoke(
        {
            "workflow_id": str(uuid4()),
            "has_shortages": True,
            "selected_plan_id": "plan-current",
            "selected_plan_version": 3,
        },
        cfg,
    )
    with pytest.raises(ValueError, match="stale"):
        graph.invoke(
            Command(
                resume={
                    "action": "approve_plan",
                    "target_id": "plan-current",
                    "target_version": 2,
                }
            ),
            cfg,
        )


def test_conflict_counterproposal_requires_approval_and_can_complete() -> None:
    graph = build_procurement_graph(InMemorySaver())
    cfg = config("counter")
    graph.invoke(
        {
            "workflow_id": str(uuid4()),
            "has_shortages": True,
            "selected_plan_id": "plan-1",
            "selected_plan_version": 1,
            "purchase_order_batch_id": "batch-1",
            "purchase_order_batch_version": 1,
            "purchase_order_ids": ["po-1"],
            "current_po_version": 1,
            "ack_has_conflict": True,
            "change_accepted": True,
        },
        cfg,
    )
    graph.invoke(
        Command(resume={"action": "approve_plan", "target_id": "plan-1", "target_version": 1}),
        cfg,
    )
    waiting_exception = graph.invoke(
        Command(
            resume={
                "action": "approve_and_send_purchase_orders",
                "target_id": "batch-1",
                "target_version": 1,
            }
        ),
        cfg,
    )
    assert waiting_exception["status"] == "WAITING_FOR_EXCEPTION_DECISION"
    waiting_counter = graph.invoke(
        Command(resume={"action": "counter", "target_id": "po-1", "target_version": 1}),
        cfg,
    )
    assert waiting_counter["status"] == "WAITING_FOR_COUNTERPROPOSAL_APPROVAL"
    completed = graph.invoke(
        Command(
            resume={
                "action": "approve_counterproposal",
                "target_id": "po-1",
                "target_version": 1,
            }
        ),
        cfg,
    )
    assert completed["status"] == "COMPLETED"


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("accept", "COMPLETED"),
        ("switch", "WAITING_FOR_PLAN_APPROVAL"),
        ("split", "WAITING_FOR_PLAN_APPROVAL"),
        ("cancel", "CANCELLED"),
    ],
)
def test_conflict_resolution_paths_are_terminal_or_return_to_sourcing(
    action: str, expected: str
) -> None:
    graph = build_procurement_graph(InMemorySaver())
    cfg = config(f"exception-{action}")
    graph.invoke(
        {
            "workflow_id": str(uuid4()),
            "has_shortages": True,
            "selected_plan_id": "plan-1",
            "selected_plan_version": 1,
            "purchase_order_batch_id": "batch-1",
            "purchase_order_batch_version": 1,
            "purchase_order_ids": ["po-1"],
            "current_po_version": 1,
            "ack_has_conflict": True,
        },
        cfg,
    )
    graph.invoke(
        Command(resume={"action": "approve_plan", "target_id": "plan-1", "target_version": 1}),
        cfg,
    )
    graph.invoke(
        Command(
            resume={
                "action": "approve_and_send_purchase_orders",
                "target_id": "batch-1",
                "target_version": 1,
            }
        ),
        cfg,
    )

    resolved = graph.invoke(
        Command(resume={"action": action, "target_id": "po-1", "target_version": 1}),
        cfg,
    )

    assert resolved["status"] == expected
