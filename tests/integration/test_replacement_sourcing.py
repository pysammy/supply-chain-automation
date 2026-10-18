"""Drive cancellation/replacement through real specialist handlers and the supplier HTTP API."""

import json
import logging
from collections import deque
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from uagents import Model
from uagents_core.contrib.protocols.chat import ChatMessage, MetadataContent, TextContent
from uagents_core.types import DeliveryStatus

from apps.supplier_simulator.main import create_app
from supplymesh.agents.acknowledgment import (
    AcknowledgmentAgentService,
    build_acknowledgment_protocol,
)
from supplymesh.agents.cancellation_transport import PurchaseOrderCancelledTransport
from supplymesh.agents.orchestration import (
    OrchestrationCoordinator,
    build_specialist_response_protocol,
)
from supplymesh.agents.orchestrator import build_chat_protocol
from supplymesh.agents.procurement import ProcurementAgentService, build_procurement_protocol
from supplymesh.agents.sourcing import SourcingAgentService, build_sourcing_protocol
from supplymesh.agents.supplier import (
    SupplierAgentClient,
    SupplierAgentService,
    build_supplier_protocol,
)
from supplymesh.agents.transport import PurchaseOrderExecutionTransport
from supplymesh.clients.supplier_client import SupplierClient
from supplymesh.domain.procurement import PurchaseOrder, PurchaseOrderLine
from supplymesh.edi.x12_860 import ChangeRequestLine, generate_860
from supplymesh.persistence.database import Base
from supplymesh.persistence.orm_models import PurchaseOrderRecord
from supplymesh.persistence.procurement_repository import ProcurementRepository
from supplymesh.persistence.side_effect_journal import SqlAlchemySideEffectJournal
from supplymesh.persistence.workflow_repository import ConnectivitySession, WorkflowRepository
from supplymesh.protocols.procurement import PurchaseOrderExecutionRequest
from supplymesh.workflows.procurement_graph import build_event_transition_graph

ORCHESTRATOR, P, S, A, V, USER = (
    "orchestrator",
    "procurement",
    "sourcing",
    "ack",
    "supplier",
    "supervisor",
)


class Bus:
    def __init__(self):
        self.handlers = {}
        self.queue = deque()
        self.chats = []
        self.sent = []
        self.block_cancellation_reply = False

    def register(self, address, protocol):
        for digest, handler in protocol.signed_message_handlers.items():
            self.handlers[address, digest] = handler

    def context(self, address):
        bus = self

        class Context:
            agent = SimpleNamespace(address=address)
            logger = logging.getLogger("replacement-test")

            async def send(self, target, message, **kwargs):
                bus.sent.append((address, target, message))
                if bus.block_cancellation_reply and isinstance(
                    message, PurchaseOrderCancelledTransport
                ):
                    return SimpleNamespace(status=DeliveryStatus.DELIVERED)
                if target == USER:
                    bus.chats.append(message)
                else:
                    bus.queue.append((address, target, message))
                return SimpleNamespace(status=DeliveryStatus.DELIVERED)

            async def send_and_receive(self, target, message, **kwargs):
                replies = []

                async def capture(target, response):
                    replies.append(response)

                handler = bus.handlers[target, Model.build_schema_digest(type(message))]
                await handler(SimpleNamespace(send=capture, logger=self.logger), address, message)
                return replies[0], SimpleNamespace(status=DeliveryStatus.DELIVERED)

        return Context()

    async def drain(self):
        while self.queue:
            sender, target, message = self.queue.popleft()
            handler = self.handlers.get((target, Model.build_schema_digest(type(message))))
            if handler:
                await handler(self.context(target), sender, message)

    async def select(self, selection, sender=USER):
        handler = self.handlers[ORCHESTRATOR, Model.build_schema_digest(ChatMessage)]
        await handler(
            self.context(ORCHESTRATOR),
            sender,
            ChatMessage(
                timestamp=datetime.now(UTC), content=[TextContent(text=json.dumps(selection))]
            ),
        )
        await self.drain()

    def card(self):
        return next(
            json.loads(content.metadata["card_payload"])
            for message in reversed(self.chats)
            if isinstance(message, ChatMessage)
            for content in message.content
            if isinstance(content, MetadataContent)
        )


@pytest.fixture
async def flow(tmp_path):
    supplier_url = f"sqlite:///{tmp_path / 'supplier.db'}"
    app = create_app(database_url=supplier_url, api_key="api", admin_api_key="admin")
    http = TestClient(app)
    http.post(
        "/api/v1/demo/scenario/price_conflict", headers={"X-API-Key": "admin"}
    ).raise_for_status()
    client = SupplierClient(
        base_url="http://test", api_key="api", transport=httpx.ASGITransport(app=app)
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'company.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    workflows = WorkflowRepository(sessions)
    procurement = ProcurementRepository(sessions)
    journal = SqlAlchemySideEffectJournal(sessions)
    workflow_id, correlation = uuid4(), uuid4()
    workflows.start_inventory(
        ConnectivitySession(
            workflow_id=workflow_id,
            conversation_id=USER,
            correlation_id=correlation,
            request_message_id=uuid4(),
            chat_message_id=uuid4(),
            status="PO_EXECUTION",
            cost_ceiling_minor=1000000,
        )
    )

    def order(number, supplier, sku, price):
        return PurchaseOrder(
            po_id=uuid4(),
            workflow_id=workflow_id,
            po_number=number,
            supplier_id=supplier,
            version=1,
            currency="USD",
            total=Decimal(price) * 2,
            lines=(
                PurchaseOrderLine(
                    line_number=1,
                    component_sku=sku,
                    quantity=2,
                    unit_price=Decimal(price),
                    requested_date=date(2026, 12, 15),
                ),
            ),
        )

    original = (
        order("ORIGINAL-ALPHA", "supplier-alpha", "ENGINE-001", "820"),
        order("ORIGINAL-BETA", "supplier-beta", "ECU-001", "260"),
    )
    approvals = [
        workflows.save_approval(
            workflow_id=workflow_id,
            decision_type="SEND_PURCHASE_ORDER",
            target_type="purchase_order",
            target_id=str(po.po_id),
            target_version=1,
            actor=USER,
            payload=po,
        )
        for po in original
    ]
    request = PurchaseOrderExecutionRequest(
        workflow_id=workflow_id,
        correlation_id=correlation,
        sender_agent="manufacturing-orchestrator",
        purchase_orders=original,
        approval_id=str(approvals[0].approval_id),
        approval_ids=tuple(str(a.approval_id) for a in approvals),
    )
    workflows.update(
        workflow_id,
        updates={
            "purchase_orders": [p.model_dump(mode="json") for p in original],
            "po_execution_request": request.model_dump(mode="json"),
            "inventory_result": {"assessment": {"required_by": "2026-12-15"}},
        },
    )
    reservations = []

    class Reservations:
        def transition_reservations(self, workflow, *, status):
            reservations.append(status)
            return 1

    coordinator = OrchestrationCoordinator(
        inventory_address="unused",
        requests=None,
        workflows=workflows,
        sourcing_address=S,
        procurement_address=P,
        acknowledgment_address=A,
        transition_graph=build_event_transition_graph(),
        reservation_store=Reservations(),
    )
    bus = Bus()
    bus.register(ORCHESTRATOR, build_chat_protocol(coordinator))
    bus.register(ORCHESTRATOR, build_specialist_response_protocol(coordinator))
    supplier = SupplierAgentService(client, ("supplier-alpha", "supplier-beta", "supplier-gamma"))
    bus.register(V, build_supplier_protocol(supplier, sourcing_address=S, procurement_address=P))
    worker = ProcurementAgentService(
        SupplierAgentClient(bus.context(P), V),
        journal,
        procurement,
        poll_interval_seconds=0.001,
        poll_timeout_seconds=1,
    )
    bus.register(P, build_procurement_protocol(worker, orchestrator_address=ORCHESTRATOR))
    bus.register(
        A,
        build_acknowledgment_protocol(
            AcknowledgmentAgentService(procurement, procurement), orchestrator_address=ORCHESTRATOR
        ),
    )

    class FX:
        async def resolve(self, sources, target):
            return {c: Decimal("1.1") if c == "EUR" else Decimal("1") for c in sources}

    bus.register(
        S,
        build_sourcing_protocol(
            SourcingAgentService(
                SupplierAgentClient(bus.context(S), V),
                ("supplier-alpha", "supplier-beta", "supplier-gamma"),
            ),
            FX(),
            orchestrator_address=ORCHESTRATOR,
        ),
    )
    bus.queue.append((ORCHESTRATOR, P, PurchaseOrderExecutionTransport.from_domain(request)))
    await bus.drain()
    assert workflows.snapshot(workflow_id).status == "WAITING_FOR_EXCEPTION_DECISION"
    return SimpleNamespace(
        bus=bus,
        coordinator=coordinator,
        workflows=workflows,
        id=workflow_id,
        original=original,
        http=http,
        sessions=sessions,
        worker=worker,
        supplier=supplier,
        reservations=reservations,
        url=supplier_url,
    )


def selection(action, po):
    return {"action": action, "target_id": str(po.po_id), "target_version": po.version}


def history(flow):
    response = flow.http.get("/api/v1/order-history", headers={"X-API-Key": "api"})
    response.raise_for_status()
    return response.json()["items"]


async def approve_replacement(flow):
    bus = flow.bus
    assert bus.card()["title"] == "Procurement plans", bus.card()
    await bus.select(bus.card()["items"][0]["primary_cta"]["selection"])
    assert bus.card()["title"] == "Approve and send purchase orders"
    # Selecting the plan prepares orders; only Approve and send can submit them.
    assert len(history(flow)) == 2
    await bus.select(bus.card()["approve_cta"]["selection"])


async def test_cancel_confirm_replacement_approvals_ack_completion_preserves_other_order(flow):
    bad, retained = flow.original[1], flow.original[0]
    bus = flow.bus
    await bus.select(selection("cancel_and_replace", bad))
    assert bus.card()["title"] == "Confirm cancellation and replacement"
    assert all(r["status"] != "CANCELLED" for r in history(flow))
    before = len(bus.chats)
    await bus.select(bus.card()["approve_cta"]["selection"])
    replies = [m for m in bus.chats[before:] if isinstance(m, ChatMessage)]
    assert len(replies) == 1  # ASI:One must receive the plans card as the first chat reply.
    assert any(isinstance(c, MetadataContent) for c in replies[0].content)
    rows = history(flow)
    assert next(r for r in rows if r["po_number"] == bad.po_number)["status"] == "CANCELLED"
    assert next(r for r in rows if r["po_number"] == retained.po_number)["status"] == "ACKNOWLEDGED"
    assert flow.reservations == []
    await approve_replacement(flow)
    state = flow.workflows.snapshot(flow.id)
    assert state.status == "COMPLETED"
    rows = history(flow)
    assert len(rows) == 3
    replacement = next(r for r in rows if "-R01-" in r["po_number"])
    assert replacement["supplier_id"] != bad.supplier_id
    assert {line["component_sku"] for line in replacement["lines"]} == {"ECU-001"}
    assert [r["po_number"] for r in rows].count(retained.po_number) == 1
    total, currency, feasible, commitments = flow.coordinator._completion_details(state)
    assert total == int((retained.total + Decimal(replacement["total"])) * 100)
    assert feasible and currency == "USD"
    assert {c["po_number"] for c in commitments} == {retained.po_number, replacement["po_number"]}
    assert flow.reservations == ["COMMITTED"]
    with flow.sessions() as session:
        assert session.get(PurchaseOrderRecord, bad.po_id).status == "CANCELLED"
    assert bus.card()["title"] == "Procurement complete"


async def test_saved_plans_reopen_without_any_supplier_side_effect(flow):
    await flow.bus.select(selection("approve_cancel_and_replace", flow.original[1]))
    state_before = flow.workflows.snapshot(flow.id)
    rows_before = history(flow)
    sent_before = len(flow.bus.sent)
    await flow.bus.select({"action": "show_plans"})
    assert flow.bus.card()["title"] == "Procurement plans"
    assert flow.workflows.snapshot(flow.id).state == state_before.state
    assert history(flow) == rows_before
    assert all(target == USER for _, target, _ in flow.bus.sent[sent_before:])
    assert len(rows_before) == 2  # Replacement sending still needs fresh approvals.


async def test_accept_exception_card_completes_with_supplier_terms_and_reopens_safely(flow):
    card = flow.bus.card()
    buttons = [node for node in card["root"]["children"] if node["type"] == "button"]
    accept = next(
        cta["action"] for cta in buttons if cta["action"]["selection"]["action"] == "accept"
    )
    assert all(not cta.get("primary") for cta in buttons)
    before = len(flow.bus.chats)
    orders = history(flow)
    await flow.bus.select({**accept["selection"], "approved": True})
    state = flow.workflows.snapshot(flow.id)
    assert state.status == "COMPLETED"
    assert flow.reservations == ["COMMITTED"]
    replies = [m for m in flow.bus.chats[before:] if isinstance(m, ChatMessage)]
    assert len(replies) == 1
    assert flow.bus.card()["title"] == "Procurement complete"
    rows = {row["label"]: row["value"] for row in flow.bus.card()["summary_rows"]}
    assert rows["Total"] == "USD 2175.60"  # Alpha 1640 plus accepted Beta 520 * 1.03.
    assert history(flow) == orders  # Accept does not create another supplier order.
    await flow.bus.select({"action": "show_completion", "target_id": str(flow.id)})
    assert flow.bus.card()["title"] == "Procurement complete"
    assert history(flow) == orders
    assert flow.reservations == ["COMMITTED"]


async def test_unconfirmed_cancellation_blocks_replacements_and_restart_replay_is_safe(flow):
    bad = flow.original[1]
    flow.bus.block_cancellation_reply = True
    await flow.bus.select(selection("approve_cancel_and_replace", bad))
    assert flow.workflows.snapshot(flow.id).status == "CANCELLING_PO"
    assert len(history(flow)) == 2
    assert flow.bus.card()["root"]["title"] == "Supplier acknowledgment exception"
    # Simulate a restart with a lost reply, not another human approval.
    flow.workflows.update(
        flow.id, updates={"cancellation_last_sent_at": "2000-01-01T00:00:00+00:00"}
    )
    flow.bus.block_cancellation_reply = False
    await flow.coordinator.recover_outbound(flow.bus.context(ORCHESTRATOR))
    await flow.bus.drain()
    assert len(history(flow)) == 2
    assert flow.workflows.snapshot(flow.id).status == "WAITING_FOR_PLAN_APPROVAL"
    await approve_replacement(flow)
    assert len(history(flow)) == 3
    assert flow.workflows.snapshot(flow.id).status == "COMPLETED"


@pytest.mark.parametrize("ceiling", [1, 164001])
async def test_budget_failure_has_retry_constraints_and_no_duplicate_original_orders(flow, ceiling):
    bad = flow.original[1]
    # Cover both an exhausted budget and a positive budget too small for a feasible plan.
    flow.workflows.update(flow.id, updates={"cost_ceiling_minor": ceiling})
    await flow.bus.select(selection("approve_cancel_and_replace", bad))
    assert flow.workflows.snapshot(flow.id).status == "WAITING_FOR_REPLACEMENT"
    assert len(history(flow)) == 2
    card = flow.bus.card()
    await flow.bus.select(card["ctas"][0]["selection"])
    retry = flow.bus.card()["submit_cta"]["selection"]
    await flow.bus.select({**retry, "cost_ceiling_minor": 1000000})
    await approve_replacement(flow)
    assert flow.workflows.snapshot(flow.id).status == "COMPLETED"


async def test_failed_supplier_cancellation_retry_blocks_replacement(flow, monkeypatch):
    original = flow.supplier.client.cancel_purchase_order

    async def unavailable(*args, **kwargs):
        raise httpx.ConnectError("supplier unavailable")

    monkeypatch.setattr(flow.supplier.client, "cancel_purchase_order", unavailable)
    await flow.bus.select(selection("approve_cancel_and_replace", flow.original[1]))
    assert flow.workflows.snapshot(flow.id).status == "CANCELLING_PO"
    assert flow.bus.card()["title"] == "Cancellation not confirmed"
    assert len(history(flow)) == 2
    assert all(r["status"] != "CANCELLED" for r in history(flow))
    assert flow.reservations == []
    monkeypatch.setattr(flow.supplier.client, "cancel_purchase_order", original)
    await flow.bus.select(flow.bus.card()["approve_cta"]["selection"])
    await approve_replacement(flow)
    assert flow.workflows.snapshot(flow.id).status == "COMPLETED"


async def test_unavailable_deadline_can_be_revised_with_fresh_quotes(flow):
    flow.workflows.update(flow.id, updates={"required_by_override": "2000-01-01"})
    await flow.bus.select(selection("approve_cancel_and_replace", flow.original[1]))
    state = flow.workflows.snapshot(flow.id)
    assert state.status == "WAITING_FOR_REPLACEMENT"
    request_id = state.state["sourcing_request"]["message_id"]
    assert len(history(flow)) == 2
    await flow.bus.select(flow.bus.card()["ctas"][0]["selection"])
    await flow.bus.select(
        {**flow.bus.card()["submit_cta"]["selection"], "required_by": "2026-12-15"}
    )
    assert flow.workflows.snapshot(flow.id).state["sourcing_request"]["message_id"] != request_id
    await approve_replacement(flow)
    assert flow.workflows.snapshot(flow.id).status == "COMPLETED"


async def test_replacement_exception_reenters_review_and_second_cancel_has_safe_fallback(flow):
    await flow.bus.select(selection("approve_cancel_and_replace", flow.original[1]))
    flow.http.post(
        "/api/v1/demo/scenario/delivery_conflict", headers={"X-API-Key": "admin"}
    ).raise_for_status()
    await approve_replacement(flow)
    state = flow.workflows.snapshot(flow.id)
    assert state.status == "WAITING_FOR_EXCEPTION_DECISION"
    replacement = PurchaseOrder.model_validate(state.state["purchase_orders"][-1])
    assert replacement.supplier_id == "supplier-gamma"
    assert flow.reservations == []
    await flow.bus.select(selection("approve_cancel_and_replace", replacement))
    assert flow.workflows.snapshot(flow.id).status == "WAITING_FOR_REPLACEMENT"
    assert len(history(flow)) == 3  # Alpha has no ECU; both eligible ECU suppliers were cancelled.
    assert flow.reservations == []
    await flow.bus.select(flow.bus.card()["ctas"][1]["selection"])
    assert flow.workflows.snapshot(flow.id).status == "CANCELLED"
    assert flow.reservations == ["RELEASED"]
    retained = next(r for r in history(flow) if r["supplier_id"] == "supplier-alpha")
    assert retained["status"] == "ACKNOWLEDGED"


async def test_wrong_user_or_stale_version_cannot_cancel_supplier_order(flow):
    bad = flow.original[1]
    await flow.bus.select(selection("approve_cancel_and_replace", bad), sender="intruder")
    await flow.bus.select({**selection("approve_cancel_and_replace", bad), "target_version": 2})
    assert all(r["status"] != "CANCELLED" for r in history(flow))
    assert flow.workflows.snapshot(flow.id).status == "WAITING_FOR_EXCEPTION_DECISION"


async def test_supplier_cancel_is_idempotent_survives_restart_and_blocks_changes(flow):
    bad = flow.original[1]
    await flow.bus.select(selection("approve_cancel_and_replace", bad))
    snapshot = flow.workflows.snapshot(flow.id)
    transaction = snapshot.state["po_submissions"][str(bad.po_id)]["supplier_transaction_id"]
    client = TestClient(create_app(database_url=flow.url, api_key="api", admin_api_key="admin"))
    path = f"/api/v1/suppliers/{bad.supplier_id}/purchase-orders/{transaction}/cancel"
    payload = {"po_number": bad.po_number, "po_version": 1}
    first = client.post(path, json=payload, headers={"X-API-Key": "api"})
    second = client.post(path, json=payload, headers={"X-API-Key": "api"})
    assert first.status_code == 200 and first.json() == second.json()
    assert (
        client.post(
            path, json={**payload, "manufacturer_id": "someone-else"}, headers={"X-API-Key": "api"}
        ).status_code
        == 422
    )
    assert (
        client.get(
            path.removesuffix("/cancel"), headers={"X-API-Key": "api", "X-Correlation-ID": "test"}
        ).json()["status"]
        == "CANCELLED"
    )
    change = {
        "message_id": str(uuid4()),
        "po_number": bad.po_number,
        "po_version": 2,
        "x12": generate_860(
            po_number=bad.po_number,
            po_version=2,
            currency="USD",
            control_number="101",
            lines=(
                ChangeRequestLine(
                    line_number=1,
                    component_sku="ECU-001",
                    quantity=2,
                    unit="EA",
                    maximum_unit_price=260,
                    requested_date=date(2026, 12, 15),
                ),
            ),
        ),
    }
    response = client.post(
        f"/api/v1/suppliers/{bad.supplier_id}/change-orders",
        json=change,
        headers={"X-API-Key": "api", "X-Correlation-ID": "test", "Idempotency-Key": "late-change"},
    )
    assert response.status_code == 422
    assert len(history(flow)) == 2
