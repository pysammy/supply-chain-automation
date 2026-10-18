from decimal import Decimal
from typing import Any

from supplymesh.cards.renderer import CardEnvelope, render_card


def completion_detail(
    *,
    workflow_id: str,
    total_cost_minor: int,
    currency: str,
    target_feasible: bool,
    commitments: list[dict[str, Any]],
    audit_references: list[str],
) -> CardEnvelope:
    return render_card(
        "detail",
        {
            "title": "Procurement complete",
            "summary_rows": [
                {"label": "Workflow", "value": workflow_id},
                {"label": "Total", "value": f"{currency} {Decimal(total_cost_minor) / 100:.2f}"},
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
            ],
        },
        informational=True,
    )


def agent_activity_detail(activities: list[dict[str, Any]]) -> CardEnvelope:
    return render_card(
        "detail",
        {
            "title": "Agent activity",
            "summary_rows": [
                {
                    "label": str(item.get("agent", "Agent")),
                    "value": f"{item.get('action', '')}: {item.get('outcome', '')}",
                }
                for item in activities
            ],
        },
        informational=True,
    )
