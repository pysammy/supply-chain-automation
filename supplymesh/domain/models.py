from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from supplymesh.domain.enums import Priority


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BomComponent(DomainModel):
    component_sku: str = Field(min_length=1, max_length=100)
    quantity_per: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    scrap_factor: Decimal = Field(default=Decimal("0"), ge=0, lt=1)
    unit: str = Field(min_length=1, max_length=20)


class InventoryPosition(DomainModel):
    component_sku: str
    on_hand: Decimal = Field(ge=0)
    confirmed_inbound: Decimal = Field(default=Decimal("0"), ge=0)
    existing_reservations: Decimal = Field(default=Decimal("0"), ge=0)
    safety_stock: Decimal = Field(default=Decimal("0"), ge=0)
    unit: str


class Demand(DomainModel):
    demand_order_id: UUID
    product_sku: str
    quantity: Decimal = Field(gt=0)
    required_by: date
    priority: Priority


class ShortageLine(DomainModel):
    component_sku: str
    unit: str
    gross_required: Decimal
    available_to_promise: Decimal
    tentative_reservation: Decimal
    shortage: Decimal
    evidence: tuple[str, ...]


class InventoryAssessment(DomainModel):
    demand_order_id: UUID
    product_sku: str
    demand_quantity: Decimal
    required_by: date
    lines: tuple[ShortageLine, ...]
    has_shortages: bool
    calculated_at: datetime


class TentativeReservation(DomainModel):
    demand_order_id: UUID
    component_sku: str
    quantity: Decimal = Field(gt=0)
    priority: Priority
    status: Literal["TENTATIVE"] = "TENTATIVE"
