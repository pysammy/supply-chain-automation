"""Exercise real payment handlers against persistent SQL and a mocked Stripe API."""

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from uagents import Model
from uagents_core.contrib.protocols.chat import ChatMessage, TextContent
from uagents_core.contrib.protocols.payment import (
    CancelPayment,
    CommitPayment,
    CompletePayment,
    Funds,
    RejectPayment,
    RequestPayment,
)
from uagents_core.registration import RegistrationRequest
from uagents_core.types import DeliveryStatus

from supplymesh.agents.factory import AgentSeeds, build_company_agents
from supplymesh.agents.orchestration import OrchestrationCoordinator
from supplymesh.agents.orchestrator import build_chat_protocol
from supplymesh.agents.payment import (
    PaymentToolRequest,
    StripeCheckout,
    build_payment_seller_protocol,
    build_payment_tool_protocol,
    request_po_payment,
)
from supplymesh.persistence.database import Base
from supplymesh.persistence.orm_models import AuditEventRecord, WorkflowRun
from supplymesh.persistence.payment_repository import PaymentRepository
from supplymesh.persistence.side_effect_journal import SqlAlchemySideEffectJournal
from supplymesh.persistence.workflow_repository import WorkflowRepository

MANAGER = "agent1manager"
ORCHESTRATOR = "agent1orchestrator"
STRIPE_CONTRACT = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "stripe_checkout_contract.json").read_text()
)


class StripeServer:
    def __init__(self):
        self.calls = []
        self.sessions = {}
        self.keys = {}
        self.parameters = {}
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request):
        self.calls.append(request)
        if request.headers.get("Stripe-Version") != STRIPE_CONTRACT["api_version"]:
            return httpx.Response(400, json={"error": {"param": "Stripe-Version"}})
        if request.method == "POST":
            form = parse_qs(request.content.decode())
            for name, values in form.items():
                constraint = STRIPE_CONTRACT["fields"].get(name)
                if constraint is None or (
                    "enum" in constraint and values[0] not in constraint["enum"]
                ):
                    return httpx.Response(
                        400, json={"error": {"param": name, "type": "invalid_request_error"}}
                    )
            key = request.headers["Idempotency-Key"]
            if key in self.keys:
                if self.parameters[key] != request.content:
                    return httpx.Response(400, json={"error": {"type": "idempotency_error"}})
                return httpx.Response(200, json=self.sessions[self.keys[key]])
            session_id = f"cs_test_{len(self.sessions) + 1}"
            session = {
                "id": session_id,
                "client_secret": "checkout_secret_example",
                "expires_at": int(time.time()) + 86400,
                "payment_status": "unpaid",
                "ui_mode": form["ui_mode"][0],
                "amount_total": int(form["line_items[0][price_data][unit_amount]"][0]),
                "currency": form["line_items[0][price_data][currency]"][0],
                "metadata": {"workflow_id": form["metadata[workflow_id]"][0]},
            }
            self.keys[key] = session_id
            self.parameters[key] = request.content
            self.sessions[session_id] = session
            return httpx.Response(200, json=session)
        return httpx.Response(200, json=self.sessions[request.url.path.rsplit("/", 1)[-1]])

    @property
    def creates(self):
        return [call for call in self.calls if call.method == "POST"]


class Context:
    def __init__(self, stripe):
        self.sent = []
        self.agent = SimpleNamespace(address=ORCHESTRATOR)
        self.logger = SimpleNamespace(warning=lambda *args, **kwargs: None)
        self.requests = []
        protocol = build_payment_tool_protocol(stripe, orchestrator_address=ORCHESTRATOR)
        self.tool = protocol.signed_message_handlers[Model.build_schema_digest(PaymentToolRequest)]

    async def send(self, destination, message, **kwargs):
        self.sent.append((destination, message))

    async def send_and_receive(self, destination, message, **kwargs):
        self.requests.append(message)
        replies = []

        async def send(_destination, response):
            replies.append(response)

        await self.tool(SimpleNamespace(send=send, logger=self.logger), ORCHESTRATOR, message)
        return replies[0], SimpleNamespace(status=DeliveryStatus.DELIVERED)


@dataclass
class Setup:
    path: Path
    workflow_id: UUID
    sessions: object
    repository: PaymentRepository
    stripe: StripeCheckout
    server: StripeServer
    context: Context


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "payments.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    workflow_id = uuid4()
    with sessions() as session, session.begin():
        session.add(
            WorkflowRun(id=workflow_id, conversation_id=MANAGER, status="COMPLETED", state={})
        )
    server = StripeServer()
    stripe = StripeCheckout(
        "sk_test_example",
        "pk_test_example",
        transport=server.transport,
        journal=SqlAlchemySideEffectJournal(sessions),
    )
    yield Setup(
        path, workflow_id, sessions, PaymentRepository(sessions), stripe, server, Context(stripe)
    )
    engine.dispose()


async def request(setup, *, workflow_id=None, context=None, repository=None, currency="USD"):
    await request_po_payment(
        context or setup.context,
        "agent1payment",
        conversation_id=MANAGER,
        workflow_id=workflow_id or setup.workflow_id,
        amount_minor=6_150_000,
        currency=currency,
        repository=repository or setup.repository,
    )


def commit_message(session_id="cs_test_1", **overrides):
    values = dict(
        funds=Funds(currency="USD", amount="61500.00", payment_method="stripe"),
        recipient=ORCHESTRATOR,
        transaction_id=session_id,
    )
    values.update(overrides)
    return CommitPayment(**values)


def commit_handler(repository):
    return build_payment_seller_protocol("agent1payment", repository).signed_message_handlers[
        Model.build_schema_digest(CommitPayment)
    ]


def latest_payment_request(context):
    return next(
        message for _, message in reversed(context.sent) if isinstance(message, RequestPayment)
    )


async def test_checkout_uses_confirmed_amount_and_survives_restart_without_recreating(setup):
    await request(setup)
    outgoing = latest_payment_request(setup.context)
    assert isinstance(outgoing, RequestPayment)
    assert outgoing.accepted_funds[0].amount == "61500.00"
    assert outgoing.reference == str(setup.workflow_id)
    assert outgoing.metadata["stripe"]["checkout_session_id"] == "cs_test_1"
    form = parse_qs(setup.server.creates[0].content.decode())
    assert form["line_items[0][price_data][unit_amount]"] == ["6150000"]
    assert form["line_items[0][price_data][currency]"] == ["usd"]
    assert form["metadata[workflow_id]"] == [str(setup.workflow_id)]
    assert not any(name.startswith("payment_method_types") for name in form)
    assert form["ui_mode"] == ["embedded_page"]
    assert outgoing.metadata["stripe"]["ui_mode"] == "embedded"
    native_text = setup.context.sent[-1][1].content[0].text
    assert native_text.startswith("```json\n") and native_text.endswith("\n```")
    native = json.loads(native_text.removeprefix("```json\n").removesuffix("\n```"))
    assert native["type"] == "stripe_payment"
    assert native["client_secret"] and native["publishable_key"]
    assert native["amount"] == 6_150_000
    assert native["currency"] == "usd"
    assert native["checkout_session_id"] == outgoing.metadata["stripe"]["id"]

    engine = create_engine(f"sqlite:///{setup.path}")
    sessions = sessionmaker(engine)
    repository = PaymentRepository(sessions)
    restarted = Context(
        StripeCheckout(
            "sk_test_example",
            "pk_test_example",
            transport=setup.server.transport,
            journal=SqlAlchemySideEffectJournal(sessions),
        )
    )
    await request(setup, context=restarted, repository=repository)
    assert len(setup.server.creates) == 1
    assert isinstance(latest_payment_request(restarted), RequestPayment)
    assert repository.get(setup.workflow_id, MANAGER).checkout_session_id == "cs_test_1"
    engine.dispose()


@pytest.mark.parametrize("key_prefix", ["stripe-checkout", "stripe-checkout:v2"])
async def test_corrected_checkout_does_not_reuse_stripe_key_bound_to_rejected_parameters(
    setup, key_prefix
):
    rejected_key = sha256(f"{key_prefix}:{setup.workflow_id}".encode()).hexdigest()
    setup.server.keys[rejected_key] = "cs_rejected"
    setup.server.parameters[rejected_key] = b"ui_mode=embedded"

    await request(setup)

    assert isinstance(latest_payment_request(setup.context), RequestPayment)
    assert setup.server.creates[0].headers["Idempotency-Key"] != rejected_key
    # Existing successful journal entries retain their original lookup key.
    journal_key = sha256(f"stripe-checkout:{setup.workflow_id}".encode()).hexdigest()
    assert setup.stripe.journal.find_success(journal_key)["id"] == "cs_test_1"
    await request(setup)
    assert len(setup.server.creates) == 1


@pytest.mark.parametrize("status", [400, 401, 429, 503])
async def test_stripe_rejections_preserve_retry_key_and_expose_only_safe_diagnostics(setup, status):
    calls = []

    def reject(request):
        calls.append(request)
        return httpx.Response(
            status,
            json={
                "error": {
                    "type": "invalid_request_error",
                    "param": "ui_mode",
                    "message": "sensitive response body must stay private",
                }
            },
        )

    setup.stripe.transport = httpx.MockTransport(reject)
    for _ in range(2):
        with pytest.raises(ValueError, match=f"HTTP {status}.*param=ui_mode") as error:
            await request(setup)
        assert "sensitive" not in str(error.value)
        assert setup.repository.get(setup.workflow_id, MANAGER) is None
    assert calls[0].headers["Idempotency-Key"] == calls[1].headers["Idempotency-Key"]


async def test_saved_legacy_checkout_is_reused_without_creating_a_replacement(setup):
    key = sha256(f"stripe-checkout:{setup.workflow_id}".encode()).hexdigest()
    cached = {
        "id": "cs_legacy",
        "checkout_session_id": "cs_legacy",
        "client_secret": "legacy_secret_example",
        "publishable_key": "pk_test_example",
        "amount_cents": "6150000",
        "currency": "usd",
        "ui_mode": "embedded",
        "expires_at": str(int(time.time()) + 1800),
    }
    setup.stripe.journal.record_pending(key, "stripe_checkout", "legacy_payload")
    setup.stripe.journal.record_success(key, cached)
    await request(setup)
    assert setup.server.creates == []
    assert latest_payment_request(setup.context).metadata["stripe"]["id"] == "cs_legacy"


@pytest.mark.parametrize(
    "case",
    [
        "paid",
        "unpaid",
        "wrong_currency",
        "wrong_amount",
        "wrong_workflow",
        "wrong_sender",
        "unknown_session",
    ],
)
async def test_native_asi_chat_callback_uses_stripe_truth_and_conversation_ownership(setup, case):
    await request(setup)
    stripe_session = setup.server.sessions["cs_test_1"]
    stripe_session["payment_status"] = "unpaid" if case == "unpaid" else "paid"
    if case == "wrong_currency":
        stripe_session["currency"] = "eur"
    if case == "wrong_amount":
        stripe_session["amount_total"] = 1
    if case == "wrong_workflow":
        stripe_session["metadata"] = {"workflow_id": str(uuid4())}
    coordinator = OrchestrationCoordinator(
        inventory_address="unused",
        requests=None,
        workflows=WorkflowRepository(setup.sessions),
        payment_address="agent1payment",
        payments=setup.repository,
    )
    chat_handler = build_chat_protocol(coordinator).signed_message_handlers[
        Model.build_schema_digest(ChatMessage)
    ]
    session_id = "cs_unknown" if case == "unknown_session" else "cs_test_1"
    sender = "someone_else" if case == "wrong_sender" else MANAGER
    message = ChatMessage(
        timestamp=datetime.now(UTC),
        content=[
            TextContent(
                text="@agent1qtfzla78vr2uznva9twcsz5xseyzs4z9paxk3zu8p49mdh5h72g92u5r6wk "
                + json.dumps({"stripe_checkout_session_id": session_id})
            )
        ],
    )
    setup.context.sent.clear()
    await chat_handler(setup.context, sender, message)
    assert setup.repository.get(setup.workflow_id, MANAGER).status == (
        "PAID" if case == "paid" else "PENDING"
    )
    assert any(isinstance(msg, CompletePayment) for _, msg in setup.context.sent) == (
        case == "paid"
    )
    if case == "paid":
        before_calls = len(setup.server.calls)
        await chat_handler(setup.context, sender, message)
        assert len(setup.server.calls) == before_calls
        with setup.sessions() as session:
            assert session.scalar(select(func.count()).select_from(AuditEventRecord)) == 1
    assert not any(
        "Confirm the demand" in part.text
        for _, msg in setup.context.sent
        if isinstance(msg, ChatMessage)
        for part in msg.content
        if isinstance(part, TextContent)
    )


async def test_verified_receipt_and_replayed_confirmation_are_durable_and_idempotent(setup):
    await request(setup)
    setup.server.sessions["cs_test_1"]["payment_status"] = "paid"
    handler = commit_handler(setup.repository)
    await handler(setup.context, MANAGER, commit_message())
    assert isinstance(setup.context.sent[-2][1], CompletePayment)
    assert "collected" in setup.context.sent[-1][1].content[0].text
    assert setup.repository.get(setup.workflow_id, MANAGER).status == "PAID"

    restarted = PaymentRepository(setup.sessions)
    before_calls = len(setup.server.calls)
    await commit_handler(restarted)(setup.context, MANAGER, commit_message())
    await request(setup, repository=restarted)
    assert len(setup.server.calls) == before_calls
    assert isinstance(setup.context.sent[-2][1], CompletePayment)
    with setup.sessions() as session:
        assert session.scalar(select(func.count()).select_from(AuditEventRecord)) == 1
        assert session.get(WorkflowRun, setup.workflow_id).status == "COMPLETED"


async def test_eur_checkout_is_collected_in_the_expected_currency(setup):
    await request(setup, currency="EUR")
    outgoing = latest_payment_request(setup.context)
    assert outgoing.accepted_funds[0].currency == "EUR"
    assert setup.server.sessions["cs_test_1"]["currency"] == "eur"
    setup.server.sessions["cs_test_1"]["payment_status"] = "paid"
    await commit_handler(setup.repository)(
        setup.context,
        MANAGER,
        commit_message(funds=Funds(currency="EUR", amount="61500.00", payment_method="stripe")),
    )
    assert setup.repository.get(setup.workflow_id, MANAGER).status == "PAID"


async def test_verification_timeout_preserves_pending_payment_for_retry(setup, monkeypatch):
    await request(setup)

    async def unavailable(**kwargs):
        raise httpx.ReadTimeout("simulated Stripe timeout")

    original = setup.stripe.is_paid
    monkeypatch.setattr(setup.stripe, "is_paid", unavailable)
    await commit_handler(setup.repository)(setup.context, MANAGER, commit_message())
    assert isinstance(setup.context.sent[-1][1], CancelPayment)
    assert setup.repository.get(setup.workflow_id, MANAGER).status == "PENDING"
    monkeypatch.setattr(setup.stripe, "is_paid", original)
    setup.server.sessions["cs_test_1"]["payment_status"] = "paid"
    await commit_handler(setup.repository)(setup.context, MANAGER, commit_message())
    assert setup.repository.get(setup.workflow_id, MANAGER).status == "PAID"
    assert len(setup.server.creates) == 1


@pytest.mark.parametrize("change", ["currency", "amount", "workflow", "unpaid"])
async def test_stripe_truth_must_match_expected_currency_amount_workflow_and_paid_status(
    setup, change
):
    await request(setup)
    stripe_session = setup.server.sessions["cs_test_1"]
    stripe_session["payment_status"] = "paid"
    if change == "currency":
        stripe_session["currency"] = "eur"
    elif change == "amount":
        stripe_session["amount_total"] = 1
    elif change == "workflow":
        stripe_session["metadata"] = {"workflow_id": str(uuid4())}
    else:
        stripe_session["payment_status"] = "unpaid"
    await commit_handler(setup.repository)(setup.context, MANAGER, commit_message())
    assert isinstance(setup.context.sent[-1][1], CancelPayment)
    assert setup.repository.get(setup.workflow_id, MANAGER).status == "PENDING"


@pytest.mark.parametrize(
    "change", ["session", "sender", "recipient", "amount", "currency", "method", "reference"]
)
async def test_tampered_commit_cannot_pay_or_query_another_checkout(setup, change):
    await request(setup)
    sender = MANAGER
    values = {}
    if change == "sender":
        sender = "agent1other"
    elif change == "session":
        values["transaction_id"] = "cs_test_other"
    elif change == "recipient":
        values["recipient"] = "agent1other"
    elif change == "reference":
        values["reference"] = str(uuid4())
    else:
        values["funds"] = Funds(
            currency="EUR" if change == "currency" else "USD",
            amount="1.00" if change == "amount" else "61500.00",
            payment_method="other" if change == "method" else "stripe",
        )
    before_calls = len(setup.server.calls)
    await commit_handler(setup.repository)(setup.context, sender, commit_message(**values))
    assert isinstance(setup.context.sent[-1][1], CancelPayment)
    assert len(setup.server.calls) == before_calls
    assert setup.repository.get(setup.workflow_id, MANAGER).status == "PENDING"


async def test_multiple_workflows_in_one_chat_keep_separate_payment_sessions(setup):
    other = uuid4()
    with setup.sessions() as session, session.begin():
        session.add(WorkflowRun(id=other, conversation_id=MANAGER, status="COMPLETED", state={}))
    await request(setup)
    await request(setup, workflow_id=other)
    setup.server.sessions["cs_test_1"]["payment_status"] = "paid"
    await commit_handler(setup.repository)(setup.context, MANAGER, commit_message())
    assert setup.repository.get(setup.workflow_id, MANAGER).status == "PAID"
    assert setup.repository.get(other, MANAGER).status == "PENDING"


async def test_stripe_acceptance_before_journal_write_retries_with_identical_idempotency_key(
    setup, monkeypatch
):
    original = setup.stripe.journal.record_success
    monkeypatch.setattr(
        setup.stripe.journal,
        "record_success",
        lambda *args: (_ for _ in ()).throw(RuntimeError("crash")),
    )
    with pytest.raises(ValueError):
        await request(setup)
    monkeypatch.setattr(setup.stripe.journal, "record_success", original)
    await request(setup)
    assert len(setup.server.creates) == 2
    assert len(setup.server.sessions) == 1
    first, second = setup.server.creates
    assert first.headers["Idempotency-Key"] == second.headers["Idempotency-Key"]
    assert first.content == second.content


async def test_receipt_and_audit_roll_back_together_before_any_complete_reply(setup):
    await request(setup)
    setup.server.sessions["cs_test_1"]["payment_status"] = "paid"

    def fail_audit(*args):
        raise RuntimeError("database audit write failed")

    event.listen(AuditEventRecord, "before_insert", fail_audit)
    try:
        setup.context.sent.clear()
        await commit_handler(setup.repository)(setup.context, MANAGER, commit_message())
        assert not any(isinstance(msg, CompletePayment) for _, msg in setup.context.sent)
        assert setup.repository.get(setup.workflow_id, MANAGER).status == "PENDING"
    finally:
        event.remove(AuditEventRecord, "before_insert", fail_audit)
    await commit_handler(setup.repository)(setup.context, MANAGER, commit_message())
    assert setup.repository.get(setup.workflow_id, MANAGER).status == "PAID"
    assert len(setup.server.creates) == 1


async def test_decline_preserves_checkout_and_a_lost_commit_is_reconciled_on_resume(setup):
    await request(setup)
    reject = build_payment_seller_protocol(
        "agent1payment", setup.repository
    ).signed_message_handlers[Model.build_schema_digest(RejectPayment)]
    await reject(setup.context, MANAGER, RejectPayment(reason="later"))
    assert setup.repository.get(setup.workflow_id, MANAGER).status == "PENDING"
    setup.server.sessions["cs_test_1"]["payment_status"] = "paid"
    await request(setup)
    assert setup.repository.get(setup.workflow_id, MANAGER).status == "PAID"
    assert isinstance(setup.context.sent[-2][1], CompletePayment)
    assert len(setup.server.creates) == 1


async def test_expired_unpaid_checkout_does_not_create_a_second_payable_session(setup, monkeypatch):
    await request(setup)
    future = int(time.time()) + 172800
    monkeypatch.setattr("supplymesh.agents.payment.time.time", lambda: future)
    with pytest.raises(ValueError, match="expired"):
        await request(setup)
    assert len(setup.server.creates) == 1


async def test_internal_payment_tools_reject_non_orchestrator_sender(setup):
    await setup.context.tool(
        setup.context,
        "agent1other",
        PaymentToolRequest(
            request_id="unauthorized",
            operation="create_checkout",
            workflow_id=str(setup.workflow_id),
            amount_minor=100,
            currency="usd",
        ),
    )
    assert not setup.context.sent[-1][1].success
    assert setup.server.calls == []


def test_payment_agent_is_optional_unique_and_internal():
    seeds = [f"payment-test-{role}" for role in ("o", "i", "s", "p", "a", "v")]
    six = build_company_agents(AgentSeeds(*seeds), publish_manifests=False)
    assert len(six) == 6
    with pytest.raises(ValueError, match="STRIPE_SECRET_KEY"):
        build_company_agents(
            AgentSeeds(*seeds, payment="payment-test-pay"), publish_manifests=False
        )
    with pytest.raises(ValueError, match="unique"):
        build_company_agents(AgentSeeds(*seeds, payment=seeds[0]), publish_manifests=False)
    seven = build_company_agents(
        AgentSeeds(*seeds, payment="payment-test-pay"),
        publish_manifests=False,
        stripe_secret_key="sk_test_example",
        stripe_publishable_key="pk_test_example",
    )
    assert len(seven) == 7
    assert [agent.address for agent in seven[:6]] == [agent.address for agent in six]
    assert seven[0].get_message_protocol(Model.build_schema_digest(CommitPayment)) is not None
    for agent in seven[1:]:
        if agent is not seven[5]:
            assert agent.get_message_protocol(Model.build_schema_digest(ChatMessage)) is None
    RegistrationRequest(address=seven[6].address, name=seven[6].name)
