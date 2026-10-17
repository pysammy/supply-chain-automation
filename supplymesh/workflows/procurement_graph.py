from typing import Any, Literal, cast

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from supplymesh.workflows.state import ProcurementWorkflowState

_EVENT_TRANSITIONS: dict[str, tuple[frozenset[str], str]] = {
    "inventory_shortage": (frozenset({"INVENTORY_PLANNING"}), "SOURCING"),
    "inventory_sufficient": (frozenset({"INVENTORY_PLANNING"}), "COMPLETED"),
    "plans_ready": (frozenset({"SOURCING"}), "WAITING_FOR_PLAN_APPROVAL"),
    "plan_approved": (
        frozenset({"WAITING_FOR_PLAN_APPROVAL"}),
        "WAITING_FOR_PO_APPROVAL",
    ),
    "plan_rejected": (frozenset({"WAITING_FOR_PLAN_APPROVAL"}), "CANCELLED"),
    "purchase_orders_approved": (
        frozenset({"WAITING_FOR_PO_APPROVAL"}),
        "PO_EXECUTION",
    ),
    "purchase_orders_cancelled": (frozenset({"WAITING_FOR_PO_APPROVAL"}), "CANCELLED"),
    "purchase_orders_submitted": (
        frozenset({"PO_EXECUTION", "WAITING_FOR_SUPPLIER_ACK"}),
        "WAITING_FOR_SUPPLIER_ACK",
    ),
    "acknowledgments_matched": (
        frozenset({"WAITING_FOR_SUPPLIER_ACK"}),
        "COMPLETED",
    ),
    "acknowledgments_conflicted": (
        frozenset({"WAITING_FOR_SUPPLIER_ACK"}),
        "WAITING_FOR_EXCEPTION_DECISION",
    ),
    "exception_accepted": (
        frozenset({"WAITING_FOR_EXCEPTION_DECISION"}),
        "COMPLETED",
    ),
    "exception_cancelled": (
        frozenset({"WAITING_FOR_EXCEPTION_DECISION"}),
        "CANCELLED",
    ),
    "counterproposal_prepared": (
        frozenset({"WAITING_FOR_EXCEPTION_DECISION"}),
        "WAITING_FOR_COUNTERPROPOSAL_APPROVAL",
    ),
    "counterproposal_approved": (
        frozenset({"WAITING_FOR_COUNTERPROPOSAL_APPROVAL"}),
        "COUNTERPROPOSAL",
    ),
    "counterproposal_cancelled": (
        frozenset({"WAITING_FOR_COUNTERPROPOSAL_APPROVAL"}),
        "CANCELLED",
    ),
    "counterproposal_submitted": (
        frozenset({"COUNTERPROPOSAL", "WAITING_FOR_CHANGE_RESPONSE"}),
        "WAITING_FOR_CHANGE_RESPONSE",
    ),
    "change_accepted": (frozenset({"WAITING_FOR_CHANGE_RESPONSE"}), "COMPLETED"),
    "change_rejected": (
        frozenset({"WAITING_FOR_CHANGE_RESPONSE"}),
        "WAITING_FOR_EXCEPTION_DECISION",
    ),
    "resourcing_requested": (
        frozenset({"WAITING_FOR_EXCEPTION_DECISION"}),
        "SOURCING",
    ),
}


def apply_workflow_event(state: ProcurementWorkflowState) -> ProcurementWorkflowState:
    """Apply one externally observed event through a strict deterministic transition."""
    event = state.get("event")
    if not event or not isinstance(event.get("kind"), str):
        raise ValueError("workflow event kind is required")
    kind = event["kind"]
    transition = _EVENT_TRANSITIONS.get(kind)
    if transition is None:
        raise ValueError(f"unsupported workflow event: {kind}")
    allowed, next_status = transition
    current = state.get("status")
    if current not in allowed:
        raise ValueError(f"workflow event {kind} is invalid while status is {current}")
    return {"status": next_status, "event": {}}


def build_event_transition_graph() -> Any:
    """Build the LangGraph used by transport callbacks to advance durable workflow state."""
    graph = StateGraph(ProcurementWorkflowState)
    graph.add_node("apply_event", apply_workflow_event)
    graph.add_edge(START, "apply_event")
    graph.add_edge("apply_event", END)
    return graph.compile()


def _status(value: str) -> ProcurementWorkflowState:
    return {"status": value}


def _intake(state: ProcurementWorkflowState) -> ProcurementWorkflowState:
    del state
    return _status("INVENTORY_PLANNING")


def _inventory(state: ProcurementWorkflowState) -> ProcurementWorkflowState:
    return _status("SOURCING" if state.get("has_shortages", False) else "COMPLETED")


def _after_inventory(state: ProcurementWorkflowState) -> Literal["sourcing", "completed"]:
    return "sourcing" if state["status"] == "SOURCING" else "completed"


def _sourcing(state: ProcurementWorkflowState) -> ProcurementWorkflowState:
    del state
    return _status("WAITING_FOR_PLAN_APPROVAL")


def _wait_plan(state: ProcurementWorkflowState) -> ProcurementWorkflowState:
    del state
    return _status("WAITING_FOR_PLAN_APPROVAL")


def _plan_approval(state: ProcurementWorkflowState) -> ProcurementWorkflowState:
    selection = cast(
        dict[str, Any],
        interrupt(
            {
                "kind": "plan_approval",
                "target_id": state.get("selected_plan_id"),
                "target_version": state.get("selected_plan_version"),
            }
        ),
    )
    _validate_selection(
        selection, state.get("selected_plan_id"), state.get("selected_plan_version")
    )
    action = selection["action"]
    if action == "approve_plan":
        return {"status": "WAITING_FOR_PO_APPROVAL", "decision": action}
    if action == "reject_plan":
        return {"status": "CANCELLED", "decision": action}
    raise ValueError("plan approval action must be approve_plan or reject_plan")


def _after_plan(state: ProcurementWorkflowState) -> Literal["wait_po", "cancelled"]:
    return "wait_po" if state["status"] == "WAITING_FOR_PO_APPROVAL" else "cancelled"


def _wait_po(state: ProcurementWorkflowState) -> ProcurementWorkflowState:
    del state
    return _status("WAITING_FOR_PO_APPROVAL")


def _po_approval(state: ProcurementWorkflowState) -> ProcurementWorkflowState:
    selection = cast(
        dict[str, Any],
        interrupt(
            {
                "kind": "purchase_order_approval",
                "target_id": state.get("purchase_order_batch_id"),
                "target_version": state.get("purchase_order_batch_version"),
            }
        ),
    )
    _validate_selection(
        selection,
        state.get("purchase_order_batch_id"),
        state.get("purchase_order_batch_version"),
    )
    if selection["action"] != "approve_and_send_purchase_orders":
        return {"status": "CANCELLED", "decision": str(selection["action"])}
    return {"status": "PO_EXECUTION", "decision": str(selection["action"])}


def _after_po_approval(state: ProcurementWorkflowState) -> Literal["po_execution", "cancelled"]:
    return "po_execution" if state["status"] == "PO_EXECUTION" else "cancelled"


def _po_execution(state: ProcurementWorkflowState) -> ProcurementWorkflowState:
    del state
    return _status("ACK_REVIEW")


def _ack_review(state: ProcurementWorkflowState) -> ProcurementWorkflowState:
    return _status(
        "WAITING_FOR_EXCEPTION_DECISION" if state.get("ack_has_conflict", False) else "CONFIRMED"
    )


def _after_ack(state: ProcurementWorkflowState) -> Literal["wait_exception", "confirmed"]:
    return "wait_exception" if state["status"] == "WAITING_FOR_EXCEPTION_DECISION" else "confirmed"


def _wait_exception(state: ProcurementWorkflowState) -> ProcurementWorkflowState:
    del state
    return _status("WAITING_FOR_EXCEPTION_DECISION")


def _exception_decision(state: ProcurementWorkflowState) -> ProcurementWorkflowState:
    selection = cast(
        dict[str, Any],
        interrupt(
            {
                "kind": "exception_decision",
                "target_id": state.get("purchase_order_ids", [None])[0],
                "target_version": state.get("current_po_version"),
            }
        ),
    )
    target = state.get("purchase_order_ids", [None])[0]
    _validate_selection(selection, target, state.get("current_po_version"))
    action = selection["action"]
    statuses = {
        "accept": "CONFIRMED",
        "counter": "WAITING_FOR_COUNTERPROPOSAL_APPROVAL",
        "switch": "SOURCING",
        "split": "SOURCING",
        "cancel": "CANCELLED",
    }
    if action not in statuses:
        raise ValueError("unsupported exception action")
    return {"status": statuses[action], "decision": str(action)}


def _after_exception(
    state: ProcurementWorkflowState,
) -> Literal["confirmed", "wait_counter", "sourcing", "cancelled"]:
    return {
        "CONFIRMED": "confirmed",
        "WAITING_FOR_COUNTERPROPOSAL_APPROVAL": "wait_counter",
        "SOURCING": "sourcing",
        "CANCELLED": "cancelled",
    }[state["status"]]


def _wait_counter(state: ProcurementWorkflowState) -> ProcurementWorkflowState:
    del state
    return _status("WAITING_FOR_COUNTERPROPOSAL_APPROVAL")


def _counter_approval(state: ProcurementWorkflowState) -> ProcurementWorkflowState:
    selection = cast(
        dict[str, Any],
        interrupt(
            {
                "kind": "counterproposal_approval",
                "target_id": state.get("purchase_order_ids", [None])[0],
                "target_version": state.get("current_po_version"),
            }
        ),
    )
    target = state.get("purchase_order_ids", [None])[0]
    _validate_selection(selection, target, state.get("current_po_version"))
    if selection["action"] != "approve_counterproposal":
        return {"status": "CANCELLED"}
    return _status("COUNTERPROPOSAL")


def _after_counter_approval(
    state: ProcurementWorkflowState,
) -> Literal["counterproposal", "cancelled"]:
    return "counterproposal" if state["status"] == "COUNTERPROPOSAL" else "cancelled"


def _counterproposal(state: ProcurementWorkflowState) -> ProcurementWorkflowState:
    return {
        "status": "ACK_REVIEW",
        "ack_has_conflict": not state.get("change_accepted", False),
    }


def _confirmed(state: ProcurementWorkflowState) -> ProcurementWorkflowState:
    del state
    return _status("COMPLETED")


def _terminal(state: ProcurementWorkflowState) -> ProcurementWorkflowState:
    return state


def _validate_selection(
    selection: dict[str, Any], target_id: str | None, target_version: int | None
) -> None:
    if selection.get("target_id") != target_id or selection.get("target_version") != target_version:
        raise ValueError("decision target is stale or does not match current workflow state")


def build_procurement_graph(checkpointer: Any = None) -> Any:
    graph = StateGraph(ProcurementWorkflowState)
    graph.add_node("intake", _intake)
    graph.add_node("inventory", _inventory)
    graph.add_node("sourcing", _sourcing)
    graph.add_node("wait_plan", _wait_plan)
    graph.add_node("plan_approval", _plan_approval)
    graph.add_node("wait_po", _wait_po)
    graph.add_node("po_approval", _po_approval)
    graph.add_node("po_execution", _po_execution)
    graph.add_node("ack_review", _ack_review)
    graph.add_node("wait_exception", _wait_exception)
    graph.add_node("exception_decision", _exception_decision)
    graph.add_node("wait_counter", _wait_counter)
    graph.add_node("counter_approval", _counter_approval)
    graph.add_node("counterproposal", _counterproposal)
    graph.add_node("confirmed", _confirmed)
    graph.add_node("completed", _terminal)
    graph.add_node("cancelled", _terminal)
    graph.add_edge(START, "intake")
    graph.add_edge("intake", "inventory")
    graph.add_conditional_edges("inventory", _after_inventory)
    graph.add_edge("sourcing", "wait_plan")
    graph.add_edge("wait_plan", "plan_approval")
    graph.add_conditional_edges("plan_approval", _after_plan)
    graph.add_edge("wait_po", "po_approval")
    graph.add_conditional_edges("po_approval", _after_po_approval)
    graph.add_edge("po_execution", "ack_review")
    graph.add_conditional_edges("ack_review", _after_ack)
    graph.add_edge("wait_exception", "exception_decision")
    graph.add_conditional_edges("exception_decision", _after_exception)
    graph.add_edge("wait_counter", "counter_approval")
    graph.add_conditional_edges("counter_approval", _after_counter_approval)
    graph.add_edge("counterproposal", "ack_review")
    graph.add_edge("confirmed", "completed")
    graph.add_edge("completed", END)
    graph.add_edge("cancelled", END)
    return graph.compile(checkpointer=checkpointer)
