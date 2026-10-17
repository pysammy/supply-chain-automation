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
        "custom",
        {
            "title": "Procurement complete",
            "workflow_id": workflow_id,
            "total_cost_minor": total_cost_minor,
            "currency": currency,
            "target_feasible": target_feasible,
            "commitments": commitments,
            "audit_references": audit_references,
        },
    )


def agent_activity_detail(activities: list[dict[str, Any]]) -> CardEnvelope:
    return render_card(
        "custom",
        {"title": "Agent activity", "activities": activities},
    )
