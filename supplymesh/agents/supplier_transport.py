"""Small RPC boundary between company agents and the Supplier Agent."""

from typing import Literal
from uuid import uuid4

from uagents import Model

SupplierOperation = Literal[
    "capacity", "rfq", "submit_po", "po_status", "submit_change", "change_status", "cancel_po"
]


class SupplierToolRequest(Model):
    schema_version: Literal["1.0"] = "1.0"
    request_id: str
    correlation_id: str
    supplier_id: str
    operation: SupplierOperation
    idempotency_key: str | None = None
    payload_json: str = "{}"


class SupplierToolResult(Model):
    schema_version: Literal["1.0"] = "1.0"
    request_id: str
    correlation_id: str
    supplier_id: str
    operation: SupplierOperation
    success: bool
    payload_json: str = "{}"
    error: str | None = None


def tool_request(
    operation: SupplierOperation,
    supplier_id: str,
    correlation_id: str,
    payload_json: str,
    idempotency_key: str | None = None,
) -> SupplierToolRequest:
    return SupplierToolRequest(
        request_id=str(uuid4()),
        correlation_id=correlation_id,
        supplier_id=supplier_id,
        operation=operation,
        idempotency_key=idempotency_key,
        payload_json=payload_json,
    )
