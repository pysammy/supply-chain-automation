from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from supplymesh.cards.renderer import CardEnvelope, render_card


class DemandIntake(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_sku: str = Field(min_length=1, max_length=100)
    quantity: int = Field(gt=0)
    required_by: date
    priority: Literal["normal", "high", "rush"] = "normal"
    cost_ceiling_minor: int | None = Field(default=None, gt=0)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")


def demand_intake_card() -> CardEnvelope:
    return render_card(
        "form",
        {
            "title": "Manufacturing demand",
            "fields": [
                {"name": "product_sku", "label": "Product SKU", "kind": "text", "required": True},
                {"name": "quantity", "label": "Quantity", "kind": "number", "required": True},
                {
                    "name": "required_by",
                    "label": "Required by (YYYY-MM-DD)",
                    "kind": "text",
                    "required": True,
                },
                {
                    "name": "priority",
                    "label": "Priority",
                    "kind": "select",
                    "options": [
                        {"value": item, "label": item.title()}
                        for item in ("normal", "high", "rush")
                    ],
                    "required": True,
                },
                {"name": "cost_ceiling_minor", "label": "Cost ceiling", "kind": "number"},
            ],
            "submit_cta": {"label": "Assess inventory", "selection": {"action": "submit_demand"}},
        },
    )
