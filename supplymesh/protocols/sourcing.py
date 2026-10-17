from typing import Literal

from supplymesh.domain.sourcing import OptimizationResult, SupplierQuote
from supplymesh.protocols.common import MessageEnvelope


class SourcingRequest(MessageEnvelope):
    message_type: Literal["SourcingRequest"] = "SourcingRequest"
    shortages: dict[str, int]
    required_by: dict[str, str]
    currency: str = "USD"


class SupplierQuotesCollected(MessageEnvelope):
    message_type: Literal["SupplierQuotesCollected"] = "SupplierQuotesCollected"
    quotes: tuple[SupplierQuote, ...]
    missing_suppliers: tuple[str, ...]


class ProcurementPlansReady(MessageEnvelope):
    message_type: Literal["ProcurementPlansReady"] = "ProcurementPlansReady"
    result: OptimizationResult
