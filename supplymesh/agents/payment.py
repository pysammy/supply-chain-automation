"""Internal Payment Agent (Stripe Checkout) and the Orchestrator's Agent Payment Protocol seller."""

import json
import re
import time
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
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

from supplymesh.persistence.payment_repository import PaymentRepository, PaymentState
from supplymesh.services.approval_service import approval_payload_hash
from supplymesh.services.procurement_service import SideEffectJournal

STRIPE_SESSIONS_URL = "https://api.stripe.com/v1/checkout/sessions"
STRIPE_API_VERSION = "2026-09-30.endive"
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
    """Embedded Stripe Checkout through Stripe's REST API."""

    def __init__(
        self,
        secret_key: str,
        publishable_key: str,
        *,
        return_url: str = "https://asi1.ai",
        journal: SideEffectJournal | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.secret_key = secret_key
        self.publishable_key = publishable_key
        self.return_url = return_url
        self.transport = transport
        self.journal = journal

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            auth=(self.secret_key, ""),
            headers={"Stripe-Version": STRIPE_API_VERSION},
            timeout=15,
            transport=self.transport,
        )

    async def create(
        self, *, workflow_id: str, amount_minor: int, currency: str, description: str
    ) -> dict[str, str]:
        if currency not in {"usd", "eur"} or not 0 < amount_minor <= 99_999_999:
            raise ValueError("Checkout requires a positive USD/EUR amount within Stripe limits")
        UUID(workflow_id)
        key = sha256(f"stripe-checkout:{workflow_id}".encode()).hexdigest()
        payload = {
            "workflow_id": workflow_id,
            "amount_minor": amount_minor,
            "currency": currency,
            "description": description,
            "return_url": self.return_url,
        }
        if self.journal is not None:
            cached = self.journal.find_success(key)
            if cached is not None:
                if cached["currency"] != currency or cached["amount_cents"] != str(amount_minor):
                    raise ValueError("checkout amount or currency has changed")
                return cached
            self.journal.record_pending(key, "stripe_checkout", approval_payload_hash(payload))
        separator = "&" if "?" in self.return_url else "?"
        data = {
            "ui_mode": "embedded_page",
            "redirect_on_completion": "if_required",
            "return_url": f"{self.return_url}{separator}session_id={{CHECKOUT_SESSION_ID}}",
            "mode": "payment",
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": currency,
            "line_items[0][price_data][unit_amount]": str(amount_minor),
            "line_items[0][price_data][product_data][name]": "SupplyMesh purchase orders",
            "line_items[0][price_data][product_data][description]": description,
            "metadata[workflow_id]": workflow_id,
        }
        # Bind Stripe retries to the complete wire contract, not just a manually bumped version.
        # The durable journal key above stays stable to preserve already saved sessions.
        stripe_key = approval_payload_hash({"api_version": STRIPE_API_VERSION, "data": data})
        async with self._client() as client:
            response = await client.post(
                STRIPE_SESSIONS_URL,
                headers={"Idempotency-Key": stripe_key},
                data=data,
            )
            response.raise_for_status()
            session: dict[str, Any] = response.json()
        if (
            not isinstance(session.get("id"), str)
            or not CHECKOUT_SESSION_ID.fullmatch(session["id"])
            or not isinstance(session.get("client_secret"), str)
            or not session["client_secret"]
            or session.get("amount_total") != amount_minor
            or session.get("currency") != currency
            or (session.get("metadata") or {}).get("workflow_id") != workflow_id
            or session.get("ui_mode") != "embedded_page"
            or not isinstance(session.get("expires_at"), int)
        ):
            raise ValueError("Stripe returned invalid checkout details")
        # Payment Protocol metadata only allows string values.
        checkout = {
            "client_secret": str(session["client_secret"]),
            "id": str(session["id"]),
            "checkout_session_id": str(session["id"]),
            "publishable_key": self.publishable_key,
            "currency": currency,
            "amount_cents": str(amount_minor),
            # Fetch's RequestPayment renderer uses this label; it is not a Stripe API enum.
            "ui_mode": "embedded",
            "expires_at": str(session["expires_at"]),
        }
        if self.journal is not None:
            self.journal.record_success(key, checkout)
        return checkout

    async def is_paid(
        self, *, session_id: str, workflow_id: str, amount_minor: int, currency: str
    ) -> bool:
        if not CHECKOUT_SESSION_ID.fullmatch(session_id):
            return False
        async with self._client() as client:
            response = await client.get(f"{STRIPE_SESSIONS_URL}/{session_id}")
            response.raise_for_status()
            session: dict[str, Any] = response.json()
        return (
            session.get("payment_status") == "paid"
            and session.get("amount_total") == amount_minor
            and session.get("currency") == currency.lower()
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
                    currency=msg.currency,
                )
                result = PaymentToolResult(request_id=msg.request_id, success=True, paid=paid)
        except httpx.HTTPStatusError as exc:
            # Log only safe diagnostic fields, never the response body, keys or client secret.
            try:
                error = exc.response.json().get("error", {})
            except (ValueError, AttributeError):
                error = {}
            details = [f"HTTP {exc.response.status_code}"]
            if isinstance(error, dict):
                for field in ("type", "code", "param"):
                    value = error.get(field)
                    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_\[\].-]{1,120}", value):
                        details.append(f"{field}={value}")
            diagnostic = ", ".join(details)
            ctx.logger.warning(f"Stripe request rejected: {diagnostic}")
            result = PaymentToolResult(
                request_id=msg.request_id,
                success=False,
                error=f"Stripe request failed ({diagnostic}). Check the Stripe request log.",
            )
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
        raise ValueError(response.error or "Payment operation failed")
    return response


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
    repository: PaymentRepository,
) -> None:
    """Ask the manager to pay the confirmed purchase-order total through Stripe."""
    currency = currency.lower()
    pending = repository.get(workflow_id, conversation_id)
    if pending is not None:
        if pending.amount_minor != amount_minor or pending.currency != currency:
            raise ValueError("payment amount or currency no longer matches the workflow")
        if pending.status == "PAID":
            await _complete(ctx, pending)
            return
        verified = await _call_payment_agent(
            ctx,
            payment_address,
            PaymentToolRequest(
                request_id=str(uuid4()),
                operation="verify_checkout",
                workflow_id=str(workflow_id),
                amount_minor=amount_minor,
                currency=currency,
                checkout_session_id=pending.checkout_session_id,
            ),
        )
        if verified.paid:
            await _complete(ctx, repository.mark_paid(pending))
            return
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
    pending = repository.save_pending(
        PaymentState(
            workflow_id=workflow_id,
            conversation_id=conversation_id,
            checkout_session_id=checkout["id"],
            amount_minor=amount_minor,
            currency=currency,
        )
    )
    if pending.status == "PAID":
        await _complete(ctx, pending)
        return
    remaining = int(checkout["expires_at"]) - int(time.time())
    if remaining <= 0:
        raise ValueError(
            "checkout has expired; operator review is required before creating a replacement"
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
            deadline_seconds=remaining,
            reference=str(workflow_id),
            description=f"Pay {_display(amount_minor, currency)} for the {description.lower()}.",
            metadata={"stripe": checkout, "service": "supplymesh_purchase_orders"},
        ),
    )
    # Direct ASI:One chat recognizes this fenced JSON and opens its native Stripe modal.
    # Protocol-aware buyers still receive RequestPayment above for the standard flow.
    await ctx.send(
        conversation_id,
        _chat(
            "```json\n"
            + json.dumps(
                {
                    "type": "stripe_payment",
                    "action": "Pay confirmed total",
                    "client_secret": checkout["client_secret"],
                    "publishable_key": checkout["publishable_key"],
                    "checkout_session_id": checkout["id"],
                    "amount": amount_minor,
                    "currency": currency,
                    "description": f"Confirmed SupplyMesh purchase orders: {workflow_id}",
                }
            )
            + "\n```"
        ),
    )


async def confirm_chat_payment(
    ctx: Context,
    payment_address: str,
    *,
    conversation_id: str,
    session_id: str,
    repository: PaymentRepository,
) -> None:
    """Handle ASI:One's native checkout callback through the same verified receipt path."""
    if not CHECKOUT_SESSION_ID.fullmatch(session_id):
        raise ValueError("invalid checkout session ID")
    pending = repository.find_session(conversation_id, session_id)
    if pending is None:
        raise ValueError("checkout does not belong to this conversation")
    await request_po_payment(
        ctx,
        payment_address,
        conversation_id=conversation_id,
        workflow_id=pending.workflow_id,
        amount_minor=pending.amount_minor,
        currency=pending.currency,
        repository=repository,
    )


def _chat(text: str) -> ChatMessage:
    return ChatMessage(timestamp=datetime.now(UTC), content=[TextContent(text=text)])


async def _complete(ctx: Context, payment: PaymentState) -> None:
    await ctx.send(
        payment.conversation_id, CompletePayment(transaction_id=payment.checkout_session_id)
    )
    await ctx.send(
        payment.conversation_id,
        _chat(
            f"Payment of {_display(payment.amount_minor, payment.currency)} collected. "
            "This confirms collection into the merchant account, not supplier payouts."
        ),
    )


def build_payment_seller_protocol(payment_address: str, repository: PaymentRepository) -> Protocol:
    protocol = Protocol(spec=payment_protocol_spec, role="seller")

    @protocol.on_message(CommitPayment)
    async def commit(ctx: Context, sender: str, msg: CommitPayment) -> None:
        pending = repository.find_session(sender, msg.transaction_id)
        if (
            pending is None
            or msg.funds.payment_method != "stripe"
            or msg.recipient != str(ctx.agent.address)
            or (msg.reference is not None and msg.reference != str(pending.workflow_id))
            or msg.funds.currency.upper() != pending.currency.upper()
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
            if Decimal(msg.funds.amount) != Decimal(pending.amount_minor) / 100:
                raise ValueError("amount does not match checkout")
        except (ValueError, ArithmeticError):
            await ctx.send(
                sender,
                CancelPayment(
                    transaction_id=msg.transaction_id,
                    reason="Payment amount does not match checkout.",
                ),
            )
            return
        if pending.status == "PAID":
            await _complete(ctx, pending)
            return
        try:
            result = await _call_payment_agent(
                ctx,
                payment_address,
                PaymentToolRequest(
                    request_id=str(uuid4()),
                    operation="verify_checkout",
                    workflow_id=str(pending.workflow_id),
                    amount_minor=pending.amount_minor,
                    currency=pending.currency,
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
        try:
            paid = repository.mark_paid(pending)
        except Exception:
            await ctx.send(
                sender,
                _chat("Payment receipt could not be saved. Retry confirmation; do not pay again."),
            )
            return
        await _complete(ctx, paid)

    @protocol.on_message(RejectPayment)
    async def reject(ctx: Context, sender: str, msg: RejectPayment) -> None:
        await ctx.send(
            sender,
            _chat(
                "Payment prompt declined. Supplier commitments are unchanged. "
                "Use Pay confirmed total on the completion card to resume or check payment."
            ),
        )

    return protocol
