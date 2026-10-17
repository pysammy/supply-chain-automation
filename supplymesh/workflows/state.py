from typing import Any, TypedDict


class ProcurementWorkflowState(TypedDict, total=False):
    workflow_id: str
    conversation_id: str
    correlation_id: str
    demand_order_id: str | None
    normalized_demand: dict[str, Any] | None
    shortage_report: dict[str, Any] | None
    has_shortages: bool
    quotes: list[dict[str, Any]]
    plans: list[dict[str, Any]]
    selected_plan_id: str | None
    selected_plan_version: int | None
    purchase_order_batch_id: str | None
    purchase_order_batch_version: int | None
    approval_id: str | None
    purchase_order_ids: list[str]
    acknowledgments: list[dict[str, Any]]
    ack_has_conflict: bool
    change_accepted: bool
    exceptions: list[dict[str, Any]]
    current_po_version: int | None
    retry_counts: dict[str, int]
    status: str
    error: dict[str, Any] | None
    decision: str | None
    event: dict[str, Any]
