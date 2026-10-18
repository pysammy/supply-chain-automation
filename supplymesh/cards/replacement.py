"""Cancellation confirmation and bounded replacement sourcing controls."""

from supplymesh.cards.renderer import CardEnvelope, render_card
from supplymesh.domain.procurement import PurchaseOrder


def cancellation_review(po: PurchaseOrder, *, retry: bool = False) -> CardEnvelope:
    return render_card(
        "review",
        {
            "title": "Confirm cancellation and replacement"
            if not retry
            else "Cancellation not confirmed",
            "summary_rows": [
                {
                    "label": "Supplier PO",
                    "value": f"{po.po_number} / {po.supplier_id} / v{po.version}",
                },
                {
                    "label": "Scope",
                    "value": "Cancel this entire supplier PO and its changes. "
                    "Other supplier orders remain active.",
                },
                {
                    "label": "Next",
                    "value": "Replacement sourcing follows confirmed cancellation. "
                    "New orders require separate approvals.",
                },
            ],
            "approve_cta": {
                "label": "Approve cancellation" if not retry else "Retry cancellation",
                "selection": {
                    "action": "approve_cancel_and_replace",
                    "target_id": str(po.po_id),
                    "target_version": po.version,
                },
            },
            "reject_cta": {
                "label": "Keep waiting" if retry else "Return to exceptions",
                "selection": {"action": "dismiss_card" if retry else "show_exception"},
            },
        },
    )


def replacement_options(workflow_id: str, round_number: int, reason: str) -> CardEnvelope:
    return render_card(
        "detail",
        {
            "title": "Replacement sourcing needs review",
            "rows": [
                {"label": "Result", "value": reason},
                {
                    "label": "Existing orders",
                    "value": "This PO is cancelled. Other orders and reservations are retained.",
                },
            ],
            "ctas": [
                {
                    "label": "Retry or revise constraints",
                    "selection": {
                        "action": "replacement_constraints",
                        "target_id": workflow_id,
                        "target_version": round_number,
                    },
                },
                {
                    "label": "Stop workflow",
                    "selection": {
                        "action": "stop_replacement",
                        "target_id": workflow_id,
                        "target_version": round_number,
                    },
                },
            ],
        },
    )


def replacement_constraints(workflow_id: str, round_number: int) -> CardEnvelope:
    return render_card(
        "form",
        {
            "title": "Replacement sourcing constraints",
            "fields": [
                {
                    "name": "required_by",
                    "label": "New manufacturing deadline (YYYY-MM-DD; blank keeps current)",
                    "kind": "text",
                },
                {
                    "name": "cost_ceiling_minor",
                    "label": "New TOTAL workflow budget in minor units (blank keeps current)",
                    "kind": "number",
                },
            ],
            "submit_cta": {
                "label": "Request fresh quotations",
                "selection": {
                    "action": "retry_replacement",
                    "target_id": workflow_id,
                    "target_version": round_number,
                },
            },
        },
    )
