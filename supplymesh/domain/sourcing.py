from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field

from supplymesh.domain.models import DomainModel


class RfqLine(DomainModel):
    line_number: int = Field(ge=1)
    component_sku: str
    quantity: Decimal = Field(gt=0)
    required_by: date


class RfqRequest(DomainModel):
    rfq_id: UUID
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    valid_until: datetime
    lines: tuple[RfqLine, ...] = Field(min_length=1)


class QuoteLine(DomainModel):
    line_number: int = Field(ge=1)
    component_sku: str
    offered_quantity: Decimal = Field(ge=0)
    unit_price: Decimal = Field(gt=0)
    promised_date: date
    lead_time_days: int = Field(ge=0)
    minimum_order_quantity: Decimal = Field(gt=0)
    packaging_multiple: Decimal = Field(default=Decimal("1"), gt=0)


class SupplierQuote(DomainModel):
    supplier_id: str
    supplier_quote_ref: str
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    valid_until: datetime
    reliability_score: Decimal = Field(ge=0, le=1)
    lines: tuple[QuoteLine, ...]
    notes: tuple[str, ...] = ()


class NormalizedQuoteLine(DomainModel):
    supplier_id: str
    quote_ref: str
    component_sku: str
    offered_quantity: int = Field(ge=0)
    unit_price_minor: int = Field(gt=0)
    promised_date: date
    required_by: date
    reliability_basis_points: int = Field(ge=0, le=10_000)
    minimum_order_quantity: int = Field(gt=0)
    packaging_multiple: int = Field(gt=0)


class PlanAllocation(DomainModel):
    component_sku: str
    supplier_id: str
    quote_ref: str
    quantity: int = Field(gt=0)
    unit_price_minor: int = Field(gt=0)
    promised_date: date


class ProcurementPlan(DomainModel):
    plan_id: str
    name: Literal["cheapest", "fastest", "balanced", "risk_aware", "split"]
    allocations: tuple[PlanAllocation, ...]
    total_cost_minor: int = Field(ge=0)
    currency: str
    latest_arrival: date
    total_late_days: int = Field(ge=0)
    risk_score_basis_points: int = Field(ge=0, le=10_000)
    supplier_count: int = Field(ge=1)
    feasible: bool = True
    explanation: tuple[str, ...] = ()


class OptimizationResult(DomainModel):
    plans: tuple[ProcurementPlan, ...]
    infeasibility_reasons: tuple[str, ...] = ()


PlanName = Literal["cheapest", "fastest", "balanced", "risk_aware", "split"]
