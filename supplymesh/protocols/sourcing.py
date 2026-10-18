from typing import Literal

from pydantic import Field

from supplymesh.domain.sourcing import OptimizationResult, SupplierQuote
from supplymesh.protocols.common import MessageEnvelope


class SourcingRequest(MessageEnvelope):
    message_type: Literal["SourcingRequest"] = "SourcingRequest"
    shortages: dict[str, int]
    required_by: dict[str, str]
    currency: str = "USD"
    excluded_supplier_ids: tuple[str, ...] = ()
    cost_ceiling_minor: int | None = Field(default=None, ge=0)
    strict_deadline: bool = False


class SupplierQuotesCollected(MessageEnvelope):
    message_type: Literal["SupplierQuotesCollected"] = "SupplierQuotesCollected"
    quotes: tuple[SupplierQuote, ...]
    missing_suppliers: tuple[str, ...]


class ProcurementPlansReady(MessageEnvelope):
    message_type: Literal["ProcurementPlansReady"] = "ProcurementPlansReady"
    result: OptimizationResult
