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
            "description": "Confirm the demand before inventory planning begins.",
            "fields": [
                {"id": "product_sku", "label": "Product SKU", "type": "text", "required": True},
                {"id": "quantity", "label": "Quantity", "type": "number", "required": True},
                {"id": "required_by", "label": "Required by", "type": "date", "required": True},
                {
                    "id": "priority",
                    "label": "Priority",
                    "type": "select",
                    "options": ["normal", "high", "rush"],
                    "required": True,
                },
                {"id": "cost_ceiling_minor", "label": "Cost ceiling", "type": "number"},
            ],
            "submit": {"label": "Assess inventory", "action": "submit_demand"},
        },
    )
