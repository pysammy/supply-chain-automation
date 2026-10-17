from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import Field

from supplymesh.domain.models import DomainModel


class Severity(StrEnum):
    INFORMATIONAL = "INFORMATIONAL"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Discrepancy(DomainModel):
    category: Literal[
        "PRICE_CHANGED",
        "QUANTITY_REDUCED",
        "QUANTITY_INCREASED",
        "DELIVERY_DELAYED",
        "DELIVERY_EARLIER",
        "LINE_REJECTED",
        "SUBSTITUTION_PROPOSED",
        "UNIT_CHANGED",
        "CURRENCY_CHANGED",
        "UNKNOWN_LINE",
        "MISSING_LINE",
        "DUPLICATE_ACKNOWLEDGMENT",
        "STALE_PO_VERSION",
    ]
    severity: Severity
    po_line_number: int | None = None
    expected_value: str | None = None
    actual_value: str | None = None
    cost_impact: Decimal = Decimal("0")
    schedule_impact_days: int = 0
    unfulfilled_quantity: Decimal = Decimal("0")


class AcknowledgmentComparison(DomainModel):
    status: Literal["MATCHED", "EXCEPTION", "QUARANTINED"]
    discrepancies: tuple[Discrepancy, ...]
    absolute_cost_delta: Decimal
    percentage_cost_delta: Decimal
    unfulfilled_quantity: Decimal
    production_units_affected: Decimal
    estimated_schedule_delay_days: int = Field(ge=0)
    recommended_action: Literal["complete", "accept", "counter", "switch", "split", "cancel"]
    allowed_actions: tuple[Literal["accept", "counter", "switch", "split", "cancel"], ...]
