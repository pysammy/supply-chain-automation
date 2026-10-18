"""Internal Payment Agent (Stripe Checkout) and the Orchestrator's Agent Payment Protocol seller."""

import json
import re
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx
from uagents import Context, Model, Protocol
from uagents_core.contrib.protocols.chat import ChatMessage, TextContent
from uagents_core.contrib.protocols.payment import (
    CancelPayment,
    CommitPayment,
    CompletePayment,
    Funds,
    RejectPayment,
    RequestPayment,
    payment_protocol_spec,
)
from uagents_core.types import DeliveryStatus

from supplymesh.persistence.audit_repository import AuditRepository

STRIPE_SESSIONS_URL = "https://api.stripe.com/v1/checkout/sessions"
CHECKOUT_SESSION_ID = re.compile(r"^cs_[A-Za-z0-9_]+$")


class PaymentToolRequest(Model):
    request_id: str
    operation: Literal["create_checkout", "verify_checkout"]
    workflow_id: str
    amount_minor: int
    currency: str
    description: str = ""
    checkout_session_id: str = ""


class PaymentToolResult(Model):
    request_id: str
    success: bool
    checkout_json: str = "{}"
    paid: bool = False
    error: str | None = None


class StripeCheckout:
    """Embedded Stripe Checkout through Stripe's REST API; the secret key stays in this agent."""

    def __init__(
        self,
        secret_key: str,
        publishable_key: str,
        *,
        return_url: str = "https://agentverse.ai/payment-success",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.secret_key = secret_key
        self.publishable_key = publishable_key
        self.return_url = return_url
        self.transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(auth=(self.secret_key, ""), timeout=15, transport=self.transport)

    async def create(
        self, *, workflow_id: str, amount_minor: int, currency: str, description: str
    ) -> dict[str, str]:
        async with self._client() as client:
            response = await client.post(
                STRIPE_SESSIONS_URL,
                data={
                    "ui_mode": "embedded",
                    "redirect_on_completion": "if_required",
                    "return_url": f"{self.return_url}?session_id={{CHECKOUT_SESSION_ID}}",
                    "mode": "payment",
                    "payment_method_types[0]": "card",
                    "expires_at": str(int(time.time()) + 3600),
                    "line_items[0][quantity]": "1",
                    "line_items[0][price_data][currency]": currency,
                    "line_items[0][price_data][unit_amount]": str(amount_minor),
                    "line_items[0][price_data][product_data][name]": "SupplyMesh purchase orders",
                    "line_items[0][price_data][product_data][description]": description,
                    "metadata[workflow_id]": workflow_id,
                },
            )
            response.raise_for_status()
            session: dict[str, Any] = response.json()
        # Payment Protocol metadata only allows string values.
        return {
            "client_secret": str(session["client_secret"]),
            "id": str(session["id"]),
            "checkout_session_id": str(session["id"]),
            "publishable_key": self.publishable_key,
            "currency": currency,
            "amount_cents": str(amount_minor),
            "ui_mode": "embedded",
        }

    async def is_paid(self, *, session_id: str, workflow_id: str, amount_minor: int) -> bool:
        if not CHECKOUT_SESSION_ID.match(session_id):
            return False
        async with self._client() as client:
            response = await client.get(f"{STRIPE_SESSIONS_URL}/{session_id}")
            response.raise_for_status()
            session: dict[str, Any] = response.json()
        return (
            session.get("payment_status") == "paid"
            and session.get("amount_total") == amount_minor
            and (session.get("metadata") or {}).get("workflow_id") == workflow_id
        )


def build_payment_tool_protocol(stripe: StripeCheckout, *, orchestrator_address: str) -> Protocol:
    protocol = Protocol(name="SupplyMeshPaymentTools", version="1.0.0")

    @protocol.on_message(PaymentToolRequest, replies={PaymentToolResult})
    async def handle(ctx: Context, sender: str, msg: PaymentToolRequest) -> None:
        if sender != orchestrator_address:
            await ctx.send(
                sender,
                PaymentToolResult(
                    request_id=msg.request_id,
                    success=False,
                    error="Payments are only available through the manufacturing Orchestrator.",
                ),
            )
            return
        try:
            if msg.operation == "create_checkout":
                checkout = await stripe.create(
                    workflow_id=msg.workflow_id,
                    amount_minor=msg.amount_minor,
                    currency=msg.currency,
                    description=msg.description,
                )
                result = PaymentToolResult(
                    request_id=msg.request_id, success=True, checkout_json=json.dumps(checkout)
                )
            else:
                paid = await stripe.is_paid(
                    session_id=msg.checkout_session_id,
                    workflow_id=msg.workflow_id,
                    amount_minor=msg.amount_minor,
                )
                result = PaymentToolResult(request_id=msg.request_id, success=True, paid=paid)
        except Exception:
            ctx.logger.warning("stripe operation failed", extra={"workflow_id": msg.workflow_id})
            result = PaymentToolResult(
                request_id=msg.request_id,
                success=False,
                error="Stripe could not complete the payment operation; retry shortly.",
            )
        await ctx.send(sender, result)

    return protocol


async def _call_payment_agent(
    ctx: Context, address: str, request: PaymentToolRequest
) -> PaymentToolResult:
    response, status = await ctx.send_and_receive(
        address, request, response_type=PaymentToolResult, timeout=60
    )
    if (
        status.status != DeliveryStatus.DELIVERED
        or not isinstance(response, PaymentToolResult)
        or response.request_id != request.request_id
    ):
        raise RuntimeError("Payment Agent did not respond")
    if not response.success:
        raise RuntimeError(response.error or "Payment operation failed")
    return response


def _pending_key(conversation_id: str) -> str:
    # ponytail: one pending payment per chat user in agent storage; move into workflow
    # state if a user must pay several workflows concurrently.
    return f"pending-payment:{conversation_id}"


def _display(amount_minor: int, currency: str) -> str:
    return f"{currency.upper()} {Decimal(amount_minor) / 100:.2f}"


async def request_po_payment(
    ctx: Context,
    payment_address: str,
    *,
    conversation_id: str,
    workflow_id: UUID,
    amount_minor: int,
    currency: str,
) -> None:
    """Ask the manager to pay the confirmed purchase-order total through Stripe."""
    currency = currency.lower()
    description = f"Confirmed purchase orders for workflow {workflow_id}"
    result = await _call_payment_agent(
        ctx,
        payment_address,
        PaymentToolRequest(
            request_id=str(uuid4()),
            operation="create_checkout",
            workflow_id=str(workflow_id),
            amount_minor=amount_minor,
            currency=currency,
            description=description,
        ),
    )
    checkout: dict[str, str] = json.loads(result.checkout_json)
    ctx.storage.set(
        _pending_key(conversation_id),
        {
            "workflow_id": str(workflow_id),
            "session_id": checkout["id"],
            "amount_minor": amount_minor,
            "currency": currency,
        },
    )
    await ctx.send(
        conversation_id,
        RequestPayment(
            accepted_funds=[
                Funds(
                    currency=currency.upper(),
                    amount=f"{Decimal(amount_minor) / 100:.2f}",
                    payment_method="stripe",
                )
            ],
            recipient=str(ctx.agent.address),
            deadline_seconds=3600,
            reference=str(workflow_id),
            description=f"Pay {_display(amount_minor, currency)} for the {description.lower()}.",
            metadata={"stripe": checkout, "service": "supplymesh_purchase_orders"},
        ),
    )


def _chat(text: str) -> ChatMessage:
    return ChatMessage(timestamp=datetime.now(UTC), content=[TextContent(text=text)])


def build_payment_seller_protocol(
    payment_address: str, audit: AuditRepository | None = None
) -> Protocol:
    protocol = Protocol(spec=payment_protocol_spec, role="seller")

    @protocol.on_message(CommitPayment)
    async def commit(ctx: Context, sender: str, msg: CommitPayment) -> None:
        pending = ctx.storage.get(_pending_key(sender))
        if (
            pending is None
            or msg.funds.payment_method != "stripe"
            or msg.transaction_id != pending["session_id"]
        ):
            await ctx.send(
                sender,
                CancelPayment(
                    transaction_id=msg.transaction_id,
                    reason="This payment does not match a pending SupplyMesh purchase order.",
                ),
            )
            return
        try:
            result = await _call_payment_agent(
                ctx,
                payment_address,
                PaymentToolRequest(
                    request_id=str(uuid4()),
                    operation="verify_checkout",
                    workflow_id=pending["workflow_id"],
                    amount_minor=pending["amount_minor"],
                    currency=pending["currency"],
                    checkout_session_id=msg.transaction_id,
                ),
            )
        except Exception:
            ctx.logger.warning("payment verification failed")
            await ctx.send(
                sender,
                CancelPayment(
                    transaction_id=msg.transaction_id,
                    reason="The payment could not be verified yet; please retry.",
                ),
            )
            return
        if not result.paid:
            await ctx.send(
                sender,
                CancelPayment(
                    transaction_id=msg.transaction_id,
                    reason="Stripe has not confirmed this payment. Finish checkout and retry.",
                ),
            )
            return
        ctx.storage.remove(_pending_key(sender))
        await ctx.send(sender, CompletePayment(transaction_id=msg.transaction_id))
        if audit is not None:
            audit.append_event(
                workflow_id=UUID(pending["workflow_id"]),
                actor_type="HUMAN",
                actor_id=sender,
                action="PURCHASE_ORDERS_PAID",
                entity_type="payment",
                entity_id=msg.transaction_id,
                before=None,
                after={"amount_minor": pending["amount_minor"], "currency": pending["currency"]},
            )
        await ctx.send(
            sender,
            _chat(
                f"Payment of {_display(pending['amount_minor'], pending['currency'])} received. "
                "The confirmed purchase orders are paid."
            ),
        )

    @protocol.on_message(RejectPayment)
    async def reject(ctx: Context, sender: str, msg: RejectPayment) -> None:
        ctx.storage.remove(_pending_key(sender))
        await ctx.send(
            sender,
            _chat(
                "Payment declined. The supplier commitments stay confirmed and the "
                "purchase-order total remains outstanding."
            ),
        )

    return protocol
