"""One Supplier Agent fronts up to three supplier REST profiles."""

import asyncio
import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import Field
from uagents import Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    MetadataContent,
    TextContent,
    chat_protocol_spec,
)
from uagents_core.types import DeliveryStatus

from supplymesh.agents.supplier_transport import (
    SupplierOperation,
    SupplierToolRequest,
    SupplierToolResult,
    tool_request,
)
from supplymesh.cards.renderer import parse_card_selection, render_card
from supplymesh.cards.supplier_history import history_filters, supplier_history_card
from supplymesh.clients.supplier_client import SupplierClient
from supplymesh.domain.models import DomainModel
from supplymesh.domain.procurement import (
    ChangeOrderSubmission,
    ChangeTransactionStatus,
    ManufacturerSubmission,
    PurchaseOrderSubmission,
    SupplierTransactionAccepted,
    SupplierTransactionStatus,
)
from supplymesh.domain.sourcing import RfqLine, RfqRequest, SupplierQuote


class CapacityLookup(DomainModel):
    component_sku: str = Field(min_length=1, max_length=100)


class TransactionLookup(DomainModel):
    transaction_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class SupplierInquiry(CapacityLookup):
    supplier_id: str
    operation: Literal["rfq", "capacity"] = "rfq"
    quantity: Decimal = Field(default=Decimal("1"), gt=0)
    required_by: date | None = None


class HistoryInquiry(DomainModel):
    manufacturer_id: str | None = Field(
        default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$"
    )
    supplier_id: str | None = None
    offset: int = Field(default=0, ge=0, le=100000)


class SupplierAgentService:
    def __init__(self, client: SupplierClient, supplier_ids: tuple[str, ...]) -> None:
        if not 1 <= len(supplier_ids) <= 3 or len(set(supplier_ids)) != len(supplier_ids):
            raise ValueError("configure one to three distinct supplier profiles")
        self.client = client
        self.supplier_ids = supplier_ids

    async def execute(self, request: SupplierToolRequest) -> dict[str, Any]:
        UUID(request.request_id)
        UUID(request.correlation_id)
        if request.supplier_id not in self.supplier_ids:
            raise ValueError("unknown supplier profile")
        if len(request.payload_json.encode()) > 256 * 1024:
            raise ValueError("supplier request is too large")
        supplier_id = request.supplier_id
        correlation_id = request.correlation_id
        if request.operation in {"rfq", "submit_po", "submit_change"} and (
            not request.idempotency_key or len(request.idempotency_key) < 16
        ):
            raise ValueError("supplier command requires an idempotency key")
        if request.operation == "capacity":
            lookup = CapacityLookup.model_validate_json(request.payload_json)
            return await self.client.get_capacity(
                supplier_id, lookup.component_sku, correlation_id=correlation_id
            )
        if request.operation == "rfq":
            quote = await self.client.submit_rfq(
                supplier_id,
                RfqRequest.model_validate_json(request.payload_json),
                idempotency_key=request.idempotency_key or "",
                correlation_id=correlation_id,
            )
            return quote.model_dump(mode="json")
        if request.operation == "submit_po":
            accepted = await self.client.submit_purchase_order(
                supplier_id,
                PurchaseOrderSubmission.model_validate_json(request.payload_json),
                idempotency_key=request.idempotency_key or "",
                correlation_id=correlation_id,
            )
            return accepted.model_dump(mode="json")
        if request.operation == "submit_change":
            accepted = await self.client.submit_change_order(
                supplier_id,
                ChangeOrderSubmission.model_validate_json(request.payload_json),
                idempotency_key=request.idempotency_key or "",
                correlation_id=correlation_id,
            )
            return accepted.model_dump(mode="json")
        transaction = TransactionLookup.model_validate_json(request.payload_json)
        if request.operation == "po_status":
            status = await self.client.get_purchase_order_status(
                supplier_id, transaction.transaction_id, correlation_id=correlation_id
            )
            return status.model_dump(mode="json")
        change_status = await self.client.get_change_order_status(
            supplier_id, transaction.transaction_id, correlation_id=correlation_id
        )
        return change_status.model_dump(mode="json")

    async def history(self, inquiry: HistoryInquiry) -> dict[str, Any]:
        if inquiry.supplier_id is not None and inquiry.supplier_id not in self.supplier_ids:
            raise ValueError("unknown supplier profile")
        return await self.client.get_order_history(**inquiry.model_dump())


def build_supplier_protocol(
    service: SupplierAgentService, *, sourcing_address: str, procurement_address: str
) -> Protocol:
    protocol = Protocol(name="SupplyMeshSupplierTools", version="1.0.0")

    @protocol.on_message(SupplierToolRequest, replies={SupplierToolResult})
    async def handle_tool(ctx: Context, sender: str, msg: SupplierToolRequest) -> None:
        allowed = (
            {sourcing_address, procurement_address}
            if msg.operation in {"capacity", "rfq"}
            else {procurement_address}
        )
        result: dict[str, Any] = {}
        error = None
        if sender not in allowed:
            error = "This supplier operation is not authorized for this agent."
        else:
            try:
                result = await service.execute(msg)
            except Exception:
                ctx.logger.warning(
                    "supplier tool failed", extra={"correlation_id": msg.correlation_id}
                )
                error = (
                    "The supplier operation could not be completed; retry or request "
                    "operator review."
                )
        await ctx.send(
            sender,
            SupplierToolResult(
                request_id=msg.request_id,
                correlation_id=msg.correlation_id,
                supplier_id=msg.supplier_id,
                operation=msg.operation,
                success=error is None,
                payload_json=json.dumps(result),
                error=error,
            ),
        )

    return protocol


class SupplierAgentClient:
    """Supplier gateway using signed uAgent request/reply messages instead of HTTP."""

    def __init__(
        self,
        ctx: Context,
        address: str,
        timeout: int = 60,
        *,
        manufacturer_id: str = "manufacturer-demo",
        manufacturer_name: str = "Demo Manufacturer",
    ) -> None:
        self.ctx = ctx
        self.address = address
        self.timeout = timeout
        self.manufacturer = ManufacturerSubmission(
            manufacturer_id=manufacturer_id, manufacturer_name=manufacturer_name
        )
        # uAgents has one pending reply per sender/destination/session. Serialize the
        # RFQs sharing this context so simultaneous profiles cannot overwrite it.
        self._lock = asyncio.Lock()

    async def _call(
        self,
        operation: SupplierOperation,
        supplier_id: str,
        correlation_id: str,
        payload_json: str,
        idempotency_key: str | None = None,
    ) -> str:
        request = tool_request(
            operation, supplier_id, correlation_id, payload_json, idempotency_key
        )
        async with self._lock:
            response, status = await self.ctx.send_and_receive(
                self.address, request, response_type=SupplierToolResult, timeout=self.timeout
            )
        if status.status != DeliveryStatus.DELIVERED or not isinstance(
            response, SupplierToolResult
        ):
            raise RuntimeError(
                "Supplier Agent did not respond; retry with the same idempotency key"
            )
        if any(
            getattr(response, key) != getattr(request, key)
            for key in ("request_id", "correlation_id", "supplier_id", "operation")
        ):
            raise ValueError("supplier response does not match the request")
        if not response.success:
            raise RuntimeError(response.error or "Supplier operation failed")
        return str(response.payload_json)

    async def submit_rfq(
        self, supplier_id: str, request: RfqRequest, *, idempotency_key: str, correlation_id: str
    ) -> SupplierQuote:
        return SupplierQuote.model_validate_json(
            await self._call(
                "rfq", supplier_id, correlation_id, request.model_dump_json(), idempotency_key
            )
        )

    async def submit_purchase_order(
        self,
        supplier_id: str,
        submission: PurchaseOrderSubmission,
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> SupplierTransactionAccepted:
        submission = submission.model_copy(update=self.manufacturer.model_dump())
        return SupplierTransactionAccepted.model_validate_json(
            await self._call(
                "submit_po",
                supplier_id,
                correlation_id,
                submission.model_dump_json(),
                idempotency_key,
            )
        )

    async def get_purchase_order_status(
        self, supplier_id: str, transaction_id: str, *, correlation_id: str
    ) -> SupplierTransactionStatus:
        return SupplierTransactionStatus.model_validate_json(
            await self._call(
                "po_status",
                supplier_id,
                correlation_id,
                TransactionLookup(transaction_id=transaction_id).model_dump_json(),
            )
        )

    async def submit_change_order(
        self,
        supplier_id: str,
        submission: ChangeOrderSubmission,
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> SupplierTransactionAccepted:
        submission = submission.model_copy(update=self.manufacturer.model_dump())
        return SupplierTransactionAccepted.model_validate_json(
            await self._call(
                "submit_change",
                supplier_id,
                correlation_id,
                submission.model_dump_json(),
                idempotency_key,
            )
        )

    async def get_change_order_status(
        self, supplier_id: str, transaction_id: str, *, correlation_id: str
    ) -> ChangeTransactionStatus:
        return ChangeTransactionStatus.model_validate_json(
            await self._call(
                "change_status",
                supplier_id,
                correlation_id,
                TransactionLookup(transaction_id=transaction_id).model_dump_json(),
            )
        )


def build_supplier_chat_protocol(service: SupplierAgentService) -> Protocol:
    protocol = Protocol(spec=chat_protocol_spec)
    card = render_card(
        "form",
        {
            "title": "Supplier quotations and capacity",
            "fields": [
                {
                    "name": "operation",
                    "label": "Inquiry",
                    "kind": "select",
                    "options": [
                        {"value": "rfq", "label": "Quotation"},
                        {"value": "capacity", "label": "Capacity"},
                    ],
                    "required": True,
                },
                {
                    "name": "supplier_id",
                    "label": "Supplier",
                    "kind": "select",
                    "options": [{"value": item, "label": item} for item in service.supplier_ids],
                    "required": True,
                },
                {
                    "name": "component_sku",
                    "label": "Component SKU",
                    "kind": "text",
                    "required": True,
                },
                {"name": "quantity", "label": "Quantity (for quotations)", "kind": "number"},
                {
                    "name": "required_by",
                    "label": "Required by (YYYY-MM-DD, for quotations)",
                    "kind": "text",
                },
            ],
            "submit_cta": {"label": "Send inquiry", "selection": {"action": "supplier_inquiry"}},
        },
    )

    @protocol.on_message(ChatMessage)
    async def chat(ctx: Context, sender: str, msg: ChatMessage) -> None:
        await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=msg.msg_id))
        text = (
            "I represent "
            + ", ".join(service.supplier_ids)
            + (
                ". Use the form for quotations or capacity. Purchase orders and "
                "changes must go through the manufacturing Orchestrator. "
                "Ask 'show purchase order history' to review recorded orders."
            )
        )
        try:
            selections = [
                parse_card_selection(item.text)
                for item in msg.content
                if isinstance(item, TextContent)
            ]
            selection = next((item for item in reversed(selections) if item is not None), None)
            if selection is not None and selection.action == "dismiss_card":
                return
            if selection is not None and selection.action == "supplier_history_filters":
                await ctx.send(
                    sender,
                    ChatMessage(
                        timestamp=datetime.now(UTC),
                        content=[
                            TextContent(
                                text="Filter by manufacturer ID or supplier; blank means all."
                            ),
                            MetadataContent(
                                metadata=history_filters(service.supplier_ids).metadata()
                            ),
                        ],
                    ),
                )
                return
            raw_text = " ".join(item.text for item in msg.content if isinstance(item, TextContent))
            history_requested = selection is None and re.search(
                r"\b(history|past orders|previous orders|placed orders)\b", raw_text, re.IGNORECASE
            )
            if (
                selection is not None and selection.action == "supplier_history"
            ) or history_requested:
                values = dict(selection.values) if selection is not None else {}
                if selection is None:
                    manufacturer = re.search(
                        r"\bmanufacturer\s+([A-Za-z0-9_-]+)", raw_text, re.IGNORECASE
                    )
                    if manufacturer:
                        values["manufacturer_id"] = manufacturer[1]
                    suppliers = [item for item in service.supplier_ids if item in raw_text.lower()]
                    if len(suppliers) == 1:
                        values["supplier_id"] = suppliers[0]
                history_inquiry = HistoryInquiry.model_validate(
                    {k: v for k, v in values.items() if v not in (None, "")}
                )
                page = await service.history(history_inquiry)
                result_card = supplier_history_card(
                    page, history_inquiry.model_dump(exclude_none=True)
                )
                await ctx.send(
                    sender,
                    ChatMessage(
                        timestamp=datetime.now(UTC),
                        content=[
                            TextContent(
                                text=f"Supplier history: {len(page['items'])} record(s). "
                                "POs and changes are separate records; totals are not additive."
                            ),
                            MetadataContent(metadata=result_card.metadata()),
                        ],
                    ),
                )
                return
            if selection is not None:
                if selection.action not in {
                    "supplier_inquiry",
                    "supplier_rfq",
                    "supplier_capacity",
                }:
                    raise ValueError(
                        "Only quotations, capacity and read-only history are available in chat."
                    )
                inquiry = SupplierInquiry.model_validate(
                    {
                        key: value
                        for key, value in selection.values.items()
                        if value not in (None, "")
                    }
                )
                if selection.action == "supplier_capacity" or (
                    selection.action == "supplier_inquiry" and inquiry.operation == "capacity"
                ):
                    request = tool_request(
                        "capacity",
                        inquiry.supplier_id,
                        str(msg.msg_id),
                        CapacityLookup(component_sku=inquiry.component_sku).model_dump_json(),
                    )
                else:
                    if inquiry.required_by is None:
                        raise ValueError("A required date is needed for a quotation.")
                    rfq = RfqRequest(
                        rfq_id=msg.msg_id,
                        currency="USD",
                        valid_until=datetime.now(UTC) + timedelta(hours=1),
                        lines=(
                            RfqLine(
                                line_number=1,
                                component_sku=inquiry.component_sku,
                                quantity=inquiry.quantity,
                                required_by=inquiry.required_by,
                            ),
                        ),
                    )
                    request = tool_request(
                        "rfq",
                        inquiry.supplier_id,
                        str(msg.msg_id),
                        rfq.model_dump_json(),
                        f"chat-rfq:{sender}:{msg.msg_id}",
                    )
                result = await service.execute(request)
                text = json.dumps(result, indent=2)
        except Exception:
            text = (
                "The inquiry could not be completed. Check the filters, supplier, SKU, "
                "quantity and date, or retry if the backend is unavailable. "
                "Chat cannot submit purchase orders or changes."
            )
        await ctx.send(
            sender,
            ChatMessage(
                timestamp=datetime.now(UTC),
                content=[TextContent(text=text), MetadataContent(metadata=card.metadata())],
            ),
        )

    @protocol.on_message(ChatAcknowledgement)
    async def acknowledge(ctx: Context, sender: str, msg: ChatAcknowledgement) -> None:
        pass

    return protocol
