import json

from supplymesh.cards.renderer import CardEnvelope, render_card
from supplymesh.domain.procurement import PurchaseOrder


def _po_rows(po: PurchaseOrder) -> list[dict[str, str]]:
    return [
        {"label": f"{po.po_number} / {po.supplier_id}", "value": f"{po.currency} {po.total}"},
        *[
            {
                "label": line.component_sku,
                "value": (
                    f"{line.quantity} {line.unit} at {po.currency} {line.unit_price}, "
                    f"requested {line.requested_date}"
                ),
            }
            for line in po.lines
        ],
    ]


def purchase_order_detail(po: PurchaseOrder, *, require_send_approval: bool = True) -> CardEnvelope:
    target = {"target_id": str(po.po_id), "target_version": po.version}
    payload: dict[str, object] = {
        "title": f"Purchase order {po.po_number}",
        "summary_rows": _po_rows(po),
    }
    if require_send_approval:
        payload["approve_cta"] = {
            "label": "Approve and send",
            "primary": True,
            "selection": {"action": "approve_and_send_purchase_orders", **target},
        }
        payload["reject_cta"] = {"label": "Cancel", "selection": {"action": "cancel", **target}}
        return render_card("review", payload)
    return render_card("detail", payload, informational=True)


def counterproposal_form(*, po_id: str, po_version: int) -> CardEnvelope:
    return render_card(
        "form",
        {
            "title": "Supplier counterproposal",
            "subtitle": (
                "Leave fields blank to request original terms. "
                "Multi-line orders require blank fields."
            ),
            "fields": [
                {"name": "quantity", "label": "Acceptable quantity", "kind": "number"},
                {"name": "maximum_price", "label": "Maximum unit price", "kind": "number"},
                {"name": "required_date", "label": "Required date (YYYY-MM-DD)", "kind": "text"},
                {"name": "note", "label": "Note", "kind": "text"},
            ],
            "submit_cta": {
                "label": "Review counterproposal",
                "selection": {
                    "action": "review_counterproposal",
                    "target_id": po_id,
                    "target_version": po_version,
                },
            },
        },
    )


def purchase_order_batch_review(
    purchase_orders: tuple[PurchaseOrder, ...], *, batch_id: str, batch_version: int
) -> CardEnvelope:
    target = {"target_id": batch_id, "target_version": batch_version}
    return render_card(
        "review",
        {
            "title": "Approve and send purchase orders",
            "summary_rows": [row for po in purchase_orders for row in _po_rows(po)],
            "approve_cta": {
                "label": "Approve and send",
                "primary": True,
                "selection": {"action": "approve_and_send_purchase_orders", **target},
            },
            "reject_cta": {"label": "Cancel", "selection": {"action": "cancel", **target}},
        },
    )


def counterproposal_approval_review(
    *, po_id: str, po_version: int, values: dict[str, object]
) -> CardEnvelope:
    target = {"target_id": po_id, "target_version": po_version}
    return render_card(
        "review",
        {
            "title": "Approve supplier counterproposal",
            "summary_rows": [
                {
                    "label": key.replace("_", " ").title(),
                    "value": json.dumps(value, default=str)
                    if isinstance(value, (dict, list, tuple))
                    else str(value),
                }
                for key, value in values.items()
                if key not in {"workflow_id", "po_id"}
            ],
            "approve_cta": {
                "label": "Approve and send",
                "primary": True,
                "selection": {"action": "approve_counterproposal", **target},
            },
            "reject_cta": {"label": "Cancel", "selection": {"action": "cancel", **target}},
        },
    )
