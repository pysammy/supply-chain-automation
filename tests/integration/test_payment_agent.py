import json
from types import SimpleNamespace
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest
from uagents import Model
from uagents_core.contrib.protocols.chat import ChatMessage
from uagents_core.contrib.protocols.payment import (
    CancelPayment,
    CommitPayment,
    CompletePayment,
    Funds,
    RequestPayment,
)
from uagents_core.types import DeliveryStatus

from supplymesh.agents.factory import AgentSeeds, build_company_agents
from supplymesh.agents.payment import (
    PaymentToolRequest,
    PaymentToolResult,
    StripeCheckout,
    build_payment_seller_protocol,
    build_payment_tool_protocol,
    request_po_payment,
)

WORKFLOW_ID = uuid4()


def stripe_transport(
    calls: list[httpx.Request], payment_status: str = "paid"
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"id": "cs_test_123", "client_secret": "cs_secret"})
        return httpx.Response(
            200,
            json={
                "id": "cs_test_123",
                "payment_status": payment_status,
                "amount_total": 6150000,
                "metadata": {"workflow_id": str(WORKFLOW_ID)},
            },
        )

    return httpx.MockTransport(handler)


class FakeContext:
    """Orchestrator context whose payment-agent calls run the real tool protocol."""

    def __init__(self, stripe: StripeCheckout) -> None:
        self.sent: list[object] = []
        self.storage = SimpleNamespace(data={})
        self.storage.get = self.storage.data.get
        self.storage.set = self.storage.data.__setitem__
        self.storage.remove = lambda key: self.storage.data.pop(key, None)
        self.agent = SimpleNamespace(address="agent1orchestrator")
        self.logger = SimpleNamespace(warning=lambda *a, **k: None)
        protocol = build_payment_tool_protocol(stripe, orchestrator_address="agent1orchestrator")
        self.tool = protocol.signed_message_handlers[Model.build_schema_digest(PaymentToolRequest)]

    async def send(self, destination: str, message: object) -> None:
        self.sent.append(message)

    async def send_and_receive(self, destination, message, **kwargs):
        replies: list[object] = []
        payment_ctx = SimpleNamespace(
            send=lambda _to, reply: _append(replies, reply), logger=self.logger
        )
        await self.tool(payment_ctx, "agent1orchestrator", message)
        return replies[0], SimpleNamespace(status=DeliveryStatus.DELIVERED)


async def _append(items: list[object], item: object) -> None:
    items.append(item)


@pytest.mark.asyncio
async def test_po_total_is_requested_verified_and_completed() -> None:
    calls: list[httpx.Request] = []
    stripe = StripeCheckout("sk_test_x", "pk_test_x", transport=stripe_transport(calls))
    ctx = FakeContext(stripe)

    await request_po_payment(
        ctx,  # type: ignore[arg-type]
        "agent1payment",
        conversation_id="agent1manager",
        workflow_id=WORKFLOW_ID,
        amount_minor=6150000,
        currency="USD",
    )
    form = parse_qs(calls[0].content.decode())
    assert form["line_items[0][price_data][unit_amount]"] == ["6150000"]
    assert form["line_items[0][price_data][currency]"] == ["usd"]
    assert form["metadata[workflow_id]"] == [str(WORKFLOW_ID)]
    request = ctx.sent[-1]
    assert isinstance(request, RequestPayment)
    assert request.accepted_funds[0].amount == "61500.00"
    assert request.metadata["stripe"]["checkout_session_id"] == "cs_test_123"

    commit_handler = build_payment_seller_protocol("agent1payment").signed_message_handlers[
        Model.build_schema_digest(CommitPayment)
    ]
    funds = Funds(currency="USD", amount="61500.00", payment_method="stripe")
    await commit_handler(
        ctx,
        "agent1manager",
        CommitPayment(funds=funds, recipient="agent1orchestrator", transaction_id="cs_test_999"),
    )
    assert isinstance(ctx.sent[-1], CancelPayment)

    await commit_handler(
        ctx,
        "agent1manager",
        CommitPayment(funds=funds, recipient="agent1orchestrator", transaction_id="cs_test_123"),
    )
    assert isinstance(ctx.sent[-2], CompletePayment)
    assert isinstance(ctx.sent[-1], ChatMessage)
    assert "USD 61500.00 received" in ctx.sent[-1].content[0].text
    assert ctx.storage.data == {}


@pytest.mark.asyncio
async def test_unpaid_checkout_is_cancelled_and_stays_pending() -> None:
    stripe = StripeCheckout("sk_test_x", "pk_test_x", transport=stripe_transport([], "unpaid"))
    ctx = FakeContext(stripe)
    await request_po_payment(
        ctx,  # type: ignore[arg-type]
        "agent1payment",
        conversation_id="agent1manager",
        workflow_id=WORKFLOW_ID,
        amount_minor=6150000,
        currency="USD",
    )
    commit_handler = build_payment_seller_protocol("agent1payment").signed_message_handlers[
        Model.build_schema_digest(CommitPayment)
    ]
    await commit_handler(
        ctx,
        "agent1manager",
        CommitPayment(
            funds=Funds(currency="USD", amount="61500.00", payment_method="stripe"),
            recipient="agent1orchestrator",
            transaction_id="cs_test_123",
        ),
    )
    assert isinstance(ctx.sent[-1], CancelPayment)
    assert ctx.storage.data


@pytest.mark.asyncio
async def test_payment_tools_reject_non_orchestrator_sender() -> None:
    calls: list[httpx.Request] = []
    protocol = build_payment_tool_protocol(
        StripeCheckout("sk_test_x", "pk_test_x", transport=stripe_transport(calls)),
        orchestrator_address="agent1orchestrator",
    )
    sent: list[PaymentToolResult] = []
    ctx = SimpleNamespace(send=lambda _to, reply: _append(sent, reply))
    handler = protocol.signed_message_handlers[Model.build_schema_digest(PaymentToolRequest)]
    await handler(
        ctx,
        "agent1publicuser",
        PaymentToolRequest(
            request_id="r1",
            operation="create_checkout",
            workflow_id=str(WORKFLOW_ID),
            amount_minor=100,
            currency="usd",
        ),
    )
    assert not sent[0].success and calls == []


def test_payment_agent_is_optional_and_requires_stripe_keys() -> None:
    seeds = [f"payment-test-{role}" for role in ("o", "i", "s", "p", "a", "v")]
    assert len(build_company_agents(AgentSeeds(*seeds), publish_manifests=False)) == 6
    with pytest.raises(ValueError, match="STRIPE_SECRET_KEY"):
        build_company_agents(AgentSeeds(*seeds, payment="payment-test-pay"))
    agents = build_company_agents(
        AgentSeeds(*seeds, payment="payment-test-pay"),
        publish_manifests=False,
        stripe_secret_key="sk_test_x",
        stripe_publishable_key="pk_test_x",
    )
    orchestrator, payment = agents[0], agents[6]
    assert len(agents) == 7
    assert orchestrator.get_message_protocol(Model.build_schema_digest(CommitPayment)) is not None
    assert payment.get_message_protocol(Model.build_schema_digest(PaymentToolRequest)) is not None
    assert payment.get_message_protocol(Model.build_schema_digest(ChatMessage)) is None
    assert json.loads(PaymentToolResult(request_id="x", success=True).checkout_json) == {}
