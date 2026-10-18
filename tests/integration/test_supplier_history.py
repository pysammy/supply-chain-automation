"""Supplier-side persistence and direct ASI chat history through real HTTP/protocol handlers."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from uagents import Model
from uagents_core.contrib.protocols.chat import ChatMessage, MetadataContent, TextContent

from apps.supplier_simulator.main import create_app
from supplymesh.agents.supplier import (
    SupplierAgentClient,
    SupplierAgentService,
    build_supplier_chat_protocol,
)
from supplymesh.clients.supplier_client import SupplierClient
from supplymesh.domain.procurement import PurchaseOrder, PurchaseOrderLine, PurchaseOrderSubmission
from supplymesh.edi.x12_850 import generate_850
from supplymesh.edi.x12_855 import parse_855
from supplymesh.edi.x12_860 import ChangeRequestLine, generate_860
from supplymesh.edi.x12_865 import parse_865
from supplymesh.persistence.orm_models import SupplierOrderHistoryRecord

HEADERS = {
    "X-API-Key": "api",
    "X-Correlation-ID": "history-test",
    "Idempotency-Key": "history-order-key-0001",
}


def payload(*, number="HISTORY-001", manufacturer="maker-a", supplier="supplier-alpha"):
    po = PurchaseOrder(
        po_id=uuid4(),
        workflow_id=uuid4(),
        po_number=number,
        supplier_id=supplier,
        version=1,
        currency="USD",
        total=Decimal("1640"),
        lines=(
            PurchaseOrderLine(
                line_number=1,
                component_sku="ENGINE-001",
                quantity=2,
                unit_price=820,
                requested_date=date(2026, 12, 15),
            ),
        ),
    )
    return {
        "message_id": str(uuid4()),
        "po_number": number,
        "po_version": 1,
        "x12": generate_850(po, "100"),
        "manufacturer_id": manufacturer,
        "manufacturer_name": f"Manufacturer {manufacturer}",
    }


def submit(client, data, *, key=None, supplier="supplier-alpha"):
    return client.post(
        f"/api/v1/suppliers/{supplier}/purchase-orders",
        json=data,
        headers={**HEADERS, **({"Idempotency-Key": key} if key else {})},
    )


def history(client, **params):
    response = client.get("/api/v1/order-history", params=params, headers=HEADERS)
    assert response.status_code == 200
    return response.json()


@pytest.fixture
def backend(tmp_path):
    url = f"sqlite:///{tmp_path / 'supplier.db'}"
    app = create_app(database_url=url, api_key="api", admin_api_key="admin")
    return url, app, TestClient(app)


def test_supplier_restart_reset_and_retry_preserve_order_history_and_original_ack(backend):
    url, _, client = backend
    order = payload()
    first = submit(client, order)
    assert first.status_code == 202
    ack = client.get(first.json()["poll_url"], headers=HEADERS).json()
    assert parse_855(ack["x12"]).po_number == order["po_number"]
    # A new application/engine has no in-memory transaction data.
    restarted = TestClient(create_app(database_url=url, api_key="api", admin_api_key="admin"))
    restarted.post(
        "/api/v1/demo/scenario/price_conflict", headers={"X-API-Key": "admin"}
    ).raise_for_status()
    retry = submit(restarted, {**order, "message_id": str(uuid4())})
    assert retry.json() == first.json()
    assert restarted.get(first.json()["poll_url"], headers=HEADERS).json() == ack
    rows = history(restarted)["items"]
    assert len(rows) == 1
    row = rows[0]
    assert row["manufacturer_id"] == "maker-a"
    assert row["manufacturer_name"] == "Manufacturer maker-a"
    assert row["supplier_id"] == "supplier-alpha"
    assert Decimal(row["total"]) == Decimal("1640")
    assert row["lines"][0]["quantity"] == "2"
    assert row["lines"][0]["requested_date"] == "2026-12-15"
    assert not {"x12", "idempotency_key", "accepted_response", "status_response"} & row.keys()
    assert (
        restarted.get(
            first.json()["poll_url"].replace("supplier-alpha", "supplier-beta"), headers=HEADERS
        ).status_code
        == 404
    )
    assert (
        restarted.get(
            first.json()["poll_url"].replace("purchase-orders", "change-orders"), headers=HEADERS
        ).status_code
        == 404
    )


def test_manufacturers_are_distinct_and_filters_pagination_and_auth_work(backend):
    _, _, client = backend
    shared = payload()
    for maker in ["maker-a", "maker-b"]:
        response = submit(client, {**shared, "manufacturer_id": maker})
        assert response.status_code == 202
    other_supplier = submit(
        client, payload(manufacturer="maker-a", supplier="supplier-beta"), supplier="supplier-beta"
    )
    assert other_supplier.status_code == 202
    for index in range(10):
        assert (
            submit(
                client, payload(number=f"EXTRA-{index}"), key=f"history-extra-key-{index:04}"
            ).status_code
            == 202
        )
    first = history(client)
    assert len(first["items"]) == 10 and first["next_offset"] == 10
    second = history(client, offset=first["next_offset"])
    assert len(second["items"]) == 3 and second["next_offset"] is None
    assert not {r["transaction_id"] for r in first["items"]} & {
        r["transaction_id"] for r in second["items"]
    }
    assert len(history(client, manufacturer_id="maker-b")["items"]) == 1
    filtered = history(client, manufacturer_id="maker-a", supplier_id="supplier-beta")
    assert len(filtered["items"]) == 1
    assert history(client, manufacturer_id="no-such-maker")["items"] == []
    assert client.get("/api/v1/order-history").status_code == 401
    assert client.get("/api/v1/order-history?offset=-1", headers=HEADERS).status_code == 422


def test_reused_key_or_po_version_cannot_silently_change_terms(backend):
    _, _, client = backend
    order = payload()
    assert submit(client, order).status_code == 202
    assert submit(client, {**order, "manufacturer_name": "Changed"}).status_code == 422
    assert submit(client, order, key="another-history-key-0001").status_code == 422
    assert (
        submit(
            client, {**payload(number="NEW"), "po_number": "WRONG"}, key="new-history-key-0001"
        ).status_code
        == 422
    )
    assert len(history(client)["items"]) == 1


@pytest.mark.parametrize("reject", [False, True])
def test_change_order_history_preserves_original_order_and_restart_response(backend, reject):
    url, _, client = backend
    supplier = "supplier-beta" if reject else "supplier-alpha"
    assert submit(client, payload(supplier=supplier), supplier=supplier).status_code == 202
    if reject:
        client.post(
            "/api/v1/demo/scenario/counter_rejected", headers={"X-API-Key": "admin"}
        ).raise_for_status()
    data = {
        "message_id": str(uuid4()),
        "manufacturer_id": "maker-a",
        "manufacturer_name": "Manufacturer maker-a",
        "po_number": "HISTORY-001",
        "po_version": 2,
        "x12": generate_860(
            po_number="HISTORY-001",
            po_version=2,
            currency="USD",
            control_number="101",
            lines=(
                ChangeRequestLine(
                    line_number=1,
                    component_sku="ENGINE-001",
                    quantity=2,
                    unit="EA",
                    maximum_unit_price=800,
                    requested_date=date(2026, 12, 15),
                ),
            ),
        ),
    }
    response = client.post(
        f"/api/v1/suppliers/{supplier}/change-orders", json=data, headers=HEADERS
    )
    assert response.status_code == 202
    restarted = TestClient(create_app(database_url=url, api_key="api", admin_api_key="admin"))
    repeated = restarted.post(
        f"/api/v1/suppliers/{supplier}/change-orders", json=data, headers=HEADERS
    )
    assert repeated.json() == response.json()
    ack = restarted.get(response.json()["poll_url"], headers=HEADERS).json()
    assert parse_865(ack["x12"]).lines[0].response_code == ("IR" if reject else "IA")
    items = history(restarted)["items"]
    assert len(items) == 2
    assert {Decimal(r["total"]) for r in items} == {Decimal("1640"), Decimal("1600")}
    change = next(r for r in items if r["po_version"] == 2)
    assert change["status"] == ("CHANGE_REJECTED" if reject else "CHANGE_ACCEPTED")


def test_database_failure_does_not_acknowledge_or_leave_partial_history(backend):
    _, _, client = backend
    order = payload()

    def fail(*args):
        raise RuntimeError("simulated database failure")

    event.listen(SupplierOrderHistoryRecord, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="database failure"):
            submit(client, order)
    finally:
        event.remove(SupplierOrderHistoryRecord, "before_insert", fail)
    assert history(client)["items"] == []
    assert submit(client, order).status_code == 202


@pytest.mark.asyncio
async def test_direct_supplier_chat_history_card_filters_more_and_read_only(backend):
    _, app, client = backend
    for index in range(12):
        submit(
            client, payload(number=f"CHAT-{index}"), key=f"chat-history-key-{index:04}"
        ).raise_for_status()
    transport = httpx.ASGITransport(app=app)
    service = SupplierAgentService(
        SupplierClient(base_url="http://test", api_key="api", transport=transport),
        ("supplier-alpha", "supplier-beta", "supplier-gamma"),
    )
    handler = build_supplier_chat_protocol(service).signed_message_handlers[
        Model.build_schema_digest(ChatMessage)
    ]
    sent = []

    async def send(to, message):
        sent.append(message)

    ctx = SimpleNamespace(send=send)

    async def chat(text):
        await handler(
            ctx,
            "public-user",
            ChatMessage(timestamp=datetime.now(UTC), content=[TextContent(text=text)]),
        )
        msg = sent[-1]
        return json.loads(
            next(c.metadata["card_payload"] for c in msg.content if isinstance(c, MetadataContent))
        )

    card = await chat(
        "@sm-supplier show purchase order history for manufacturer maker-a supplier-alpha"
    )
    assert card["title"] == "Supplier order history"
    assert len(card["rows"]) == 10
    assert "Manufacturer maker-a" in card["rows"][0]["value"]
    assert "ENGINE-001: 2 EA" in card["rows"][0]["value"]
    more = next(c["selection"] for c in card["ctas"] if c["label"] == "Show more")
    second = await chat("@sm-supplier " + json.dumps(more))
    assert len(second["rows"]) == 2
    filters = await chat('{"action":"supplier_history_filters"}')
    assert filters["submit_cta"]["selection"]["action"] == "supplier_history"
    empty = await chat('@sm-supplier {"action":"supplier_history","manufacturer_id":"unknown"}')
    assert "No supplier history" in empty["rows"][0]["value"]
    assert len(history(client)["items"]) == 10  # Still 12 total, paginated; no new orders.
    assert len(history(client, offset=10)["items"]) == 2


@pytest.mark.asyncio
async def test_manufacturer_configuration_is_carried_through_supplier_agent_gateway():
    captured = []

    async def call(*args):
        captured.append(json.loads(args[3]))
        return json.dumps(
            {
                "supplier_transaction_id": "TX-1",
                "poll_url": "/poll",
                "estimated_ready_at": datetime.now(UTC).isoformat(),
            }
        )

    gateway = SupplierAgentClient(
        SimpleNamespace(),
        "supplier-address",
        manufacturer_id="urbanx",
        manufacturer_name="UrbanX Motors",
    )
    gateway._call = call
    await gateway.submit_purchase_order(
        "supplier-alpha",
        PurchaseOrderSubmission.model_validate(payload()),
        idempotency_key="a-long-enough-key",
        correlation_id=str(uuid4()),
    )
    assert captured[0]["manufacturer_id"] == "urbanx"
    assert captured[0]["manufacturer_name"] == "UrbanX Motors"
