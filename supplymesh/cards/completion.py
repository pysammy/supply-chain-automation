from decimal import Decimal
from typing import Any

from supplymesh.cards.renderer import CardEnvelope, render_card


def _activity_rows(activities: list[dict[str, Any]]) -> list[dict[str, str]]:
    names = {
        "manufacturing-orchestrator": "Orchestrator",
        "inventory-and-bom": "Inventory",
        "sourcing-and-optimization": "Sourcing",
        "procurement-execution": "Procurement",
        "acknowledgment-and-exception": "Acknowledgment",
    }
    grouped: dict[str, tuple[int, str, str]] = {}
    for item in activities:
        actor = str(item.get("agent", "Agent"))
        label = (
            "Human approvals"
            if actor.startswith(("agent1", "user1", "test-agent1"))
            else names.get(actor, actor)
        )
        count = grouped.get(label, (0, "", ""))[0]
        grouped[label] = (count + 1, str(item.get("action", "")), str(item.get("outcome", "")))
    return [
        {
            "label": label,
            "value": f"{count} recent event(s); latest {action}: {outcome}",
        }
        for label, (count, action, outcome) in grouped.items()
    ]


def completion_detail(
    *,
    workflow_id: str,
    total_cost_minor: int,
    currency: str,
    target_feasible: bool,
    commitments: list[dict[str, Any]],
    audit_references: list[str],
    activities: list[dict[str, Any]] | None = None,
    payment_enabled: bool = False,
    payment_status: str = "NOT_REQUESTED",
) -> CardEnvelope:
    return render_card(
        "detail",
        {
            "title": "Procurement complete",
            "ctas": (
                [
                    {
                        "label": "Pay confirmed total",
                        "selection": {"action": "request_payment", "target_id": workflow_id},
                        "primary": True,
                    },
                    {"label": "Done", "selection": {"action": "dismiss_card"}},
                ]
                if payment_enabled and payment_status != "PAID" and total_cost_minor > 0
                else []
            ),
            "summary_rows": [
                {"label": "Workflow", "value": workflow_id},
                {"label": "Total", "value": f"{currency} {Decimal(total_cost_minor) / 100:.2f}"},
                *(
                    [
                        {
                            "label": "Payment collected",
                            "value": "Yes" if payment_status == "PAID" else "No",
                        }
                    ]
                    if payment_enabled and total_cost_minor > 0
                    else []
                ),
                {
                    "label": "Manufacturing target feasible",
                    "value": "Yes" if target_feasible else "No",
                },
                *[
                    {
                        "label": str(item["po_number"]),
                        "value": (
                            f"{item['supplier_id']}: {currency} {item['total']}; "
                            f"arrival {item.get('latest_arrival', 'not provided')}"
                        ),
                    }
                    for item in commitments
                ],
                {"label": "Audit references", "value": ", ".join(audit_references) or "None"},
                *[
                    {"label": f"Activity: {row['label']}", "value": row["value"]}
                    for row in _activity_rows(activities or [])
                ],
            ],
        },
        informational=not (payment_enabled and payment_status != "PAID" and total_cost_minor > 0),
    )


def agent_activity_detail(activities: list[dict[str, Any]]) -> CardEnvelope:
    return render_card(
        "detail",
        {
            "title": "Agent activity",
            "summary_rows": _activity_rows(activities),
        },
        informational=True,
    )
