import asyncio
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from uagents import Model
from uagents.dispatch import dispatcher
from uagents_core.contrib.protocols.chat import ChatMessage, MetadataContent, TextContent

from apps.supplier_simulator.main import create_app
from supplymesh.agents import factory
from supplymesh.agents.factory import AgentSeeds, build_company_agents
from supplymesh.agents.sourcing import SourcingAgentService
from supplymesh.agents.supplier import (
    SupplierAgentClient,
    SupplierAgentService,
    build_supplier_chat_protocol,
)
from supplymesh.agents.supplier_transport import (
    SupplierToolResult,
)
from supplymesh.agents.transport import (
    AcknowledgmentReceivedTransport,
    DemandPlanningTransport,
    PurchaseOrderExecutionTransport,
    SourcingRequestTransport,
)
from supplymesh.clients.supplier_client import SupplierClient
from supplymesh.domain.procurement import (
    ApprovalRecord,
    Counterproposal,
    CounterproposalLine,
    PurchaseOrder,
    PurchaseOrderLine,
)
from supplymesh.edi.x12_855 import parse_855
from supplymesh.edi.x12_865 import parse_865
from supplymesh.protocols.sourcing import SourcingRequest
from supplymesh.services.approval_service import approval_payload_hash
from supplymesh.services.counterproposal_service import execute_counterproposal
from supplymesh.services.procurement_service import execute_purchase_order


class Journal:
    def __init__(self):
        self.results = {}

    def find_success(self, key):
        return self.results.get(key)

    def record_pending(self, key, operation, payload_hash):
        pass

    def record_success(self, key, result):
        self.results[key] = result


def approve(payload, decision_type):
    return ApprovalRecord(
        approval_id=uuid4(),
        workflow_id=payload.workflow_id,
        decision_type=decision_type,
        target_type="purchase_order",
        target_id=str(payload.po_id),
        target_version=1,
        decision="APPROVED",
        actor="manager",
        payload_hash=approval_payload_hash(payload),
        decided_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_real_uagent_supplier_roundtrip_three_profiles_and_edi(monkeypatch):
    app = create_app(api_key="test-api", admin_api_key="test-admin")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        (
            await http.post("/api/v1/demo/scenario/happy_path", headers={"X-API-Key": "test-admin"})
        ).raise_for_status()
    monkeypatch.setattr(
        factory, "SupplierClient", lambda **kwargs: SupplierClient(transport=transport, **kwargs)
    )
    agents = build_company_agents(
        AgentSeeds(*(f"supplier-rpc-test-{role}" for role in ("o", "i", "s", "p", "a", "vendor"))),
        supplier_base_url="http://test",
        supplier_api_key="test-api",
        publish_manifests=False,
    )
    orchestrator, inventory, sourcing, procurement, acknowledgment, supplier = agents
    task = asyncio.create_task(supplier._process_message_queue())
    try:
        sourcing_gateway = SupplierAgentClient(
            sourcing._build_context(), supplier.address, timeout=2
        )
        procurement_gateway = SupplierAgentClient(
            procurement._build_context(), supplier.address, timeout=2
        )
        request = SourcingRequest(
            correlation_id=uuid4(),
            workflow_id=uuid4(),
            sender_agent="manufacturing-orchestrator",
            shortages={"ENGINE-001": 75},
            required_by={"ENGINE-001": "2026-12-01"},
        )
        # collect() launches all three requests together: replies must remain correlated.
        collected = await SourcingAgentService(
            sourcing_gateway, ("supplier-alpha", "supplier-beta", "supplier-gamma")
        ).collect(request)
        assert {q.supplier_id for q in collected.quotes} == {
            "supplier-alpha",
            "supplier-beta",
            "supplier-gamma",
        }
        assert {q.currency for q in collected.quotes} == {"USD", "EUR"}
        capacity = json.loads(
            await sourcing_gateway._call(
                "capacity",
                "supplier-alpha",
                str(request.correlation_id),
                '{"component_sku":"ENGINE-001"}',
            )
        )
        assert capacity["available_capacity"] == "100"
        po = PurchaseOrder(
            po_id=uuid4(),
            po_number="AGENT-RPC-PO",
            workflow_id=request.workflow_id,
            supplier_id="supplier-alpha",
            version=1,
            currency="USD",
            total=Decimal("8200"),
            lines=(
                PurchaseOrderLine(
                    line_number=1,
                    component_sku="ENGINE-001",
                    quantity=Decimal("10"),
                    unit_price=Decimal("820"),
                    requested_date=date(2026, 12, 1),
                ),
            ),
        )
        approval = approve(po, "SEND_PURCHASE_ORDER")
        accepted = await execute_purchase_order(
            po, approval, procurement_gateway, Journal(), correlation_id=str(request.correlation_id)
        )
        repeated = await execute_purchase_order(
            po, approval, procurement_gateway, Journal(), correlation_id=str(request.correlation_id)
        )
        assert repeated.supplier_transaction_id == accepted.supplier_transaction_id
        status = await procurement_gateway.get_purchase_order_status(
            po.supplier_id,
            accepted.supplier_transaction_id,
            correlation_id=str(request.correlation_id),
        )
        assert parse_855(status.x12).po_number == po.po_number
        counter = Counterproposal(
            workflow_id=po.workflow_id,
            po_id=po.po_id,
            po_number=po.po_number,
            current_po_version=1,
            change_version=2,
            supplier_id=po.supplier_id,
            currency=po.currency,
            lines=(
                CounterproposalLine(
                    line_number=1,
                    component_sku="ENGINE-001",
                    quantity=Decimal("10"),
                    unit="EA",
                    maximum_unit_price=Decimal("820"),
                    requested_date=date(2026, 12, 1),
                ),
            ),
        )
        change = await execute_counterproposal(
            counter,
            approve(counter, "SEND_COUNTERPROPOSAL"),
            procurement_gateway,
            Journal(),
            correlation_id=str(request.correlation_id),
        )
        response = await procurement_gateway.get_change_order_status(
            po.supplier_id,
            change.supplier_transaction_id,
            correlation_id=str(request.correlation_id),
        )
        assert parse_865(response.x12).lines[0].response_code == "IA"
        with pytest.raises(RuntimeError, match="not authorized"):
            await sourcing_gateway._call(
                "submit_po",
                po.supplier_id,
                str(request.correlation_id),
                "{}",
                "unauthorized-command",
            )
        with pytest.raises(RuntimeError, match="not authorized"):
            await SupplierAgentClient(
                orchestrator._build_context(), supplier.address, timeout=2
            )._call(
                "rfq", po.supplier_id, str(request.correlation_id), "{}", "unauthorized-command"
            )
        with pytest.raises(RuntimeError, match="could not be completed"):
            await sourcing_gateway._call(
                "capacity",
                "unknown-supplier",
                str(request.correlation_id),
                '{"component_sku":"ENGINE-001"}',
            )
        # These are the actual factory-installed handlers. Unauthorized callers cannot
        # reach planning data, sourcing tools, approval lookup, or EDI comparison.
        for agent, message_type in (
            (inventory, DemandPlanningTransport),
            (sourcing, SourcingRequestTransport),
            (procurement, PurchaseOrderExecutionTransport),
            (acknowledgment, AcknowledgmentReceivedTransport),
        ):
            await agent._signed_message_handlers[Model.build_schema_digest(message_type)](
                agent._build_context(), supplier.address, message_type.construct()
            )
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        for agent in agents:
            dispatcher.unregister(agent.address, agent)


@pytest.mark.asyncio
async def test_supplier_chat_inquiries_call_backend_but_cannot_order():
    app = create_app(api_key="test-api", admin_api_key="test-admin")
    calls = []
    delegate = httpx.ASGITransport(app=app)

    class RecordingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            calls.append(request.url.path)
            return await delegate.handle_async_request(request)

    service = SupplierAgentService(
        SupplierClient(base_url="http://test", api_key="test-api", transport=RecordingTransport()),
        ("supplier-alpha", "supplier-beta", "supplier-gamma"),
    )
    protocol = build_supplier_chat_protocol(service)

    class Context:
        def __init__(self):
            self.sent = []

        async def send(self, destination, message):
            self.sent.append(message)

    ctx = Context()
    handler = protocol.signed_message_handlers[Model.build_schema_digest(ChatMessage)]
    for action, operation in (
        ("supplier_inquiry", "rfq"),
        ("supplier_inquiry", "capacity"),
        ("submit_po", "rfq"),
    ):
        msg = ChatMessage(
            timestamp=datetime.now(UTC),
            content=[
                TextContent(
                    text=json.dumps(
                        {
                            "action": action,
                            **(
                                {
                                    "operation": operation,
                                    "supplier_id": "supplier-alpha",
                                    "component_sku": "ENGINE-001",
                                    "quantity": 10,
                                    "required_by": "2026-12-01",
                                }
                                if action == "supplier_inquiry"
                                else {}
                            ),
                        }
                    )
                )
            ],
        )
        await handler(ctx, "agent1publicmanager", msg)
    assert calls == [
        "/api/v1/suppliers/supplier-alpha/rfqs",
        "/api/v1/suppliers/supplier-alpha/capacity",
    ]
    assert "cannot submit purchase orders" in ctx.sent[-1].content[0].text
    assert any(isinstance(item, MetadataContent) for item in ctx.sent[-1].content)
    metadata = next(item for item in ctx.sent[-1].content if isinstance(item, MetadataContent))
    assert set(json.loads(metadata.metadata["card_payload"])) == {"title", "fields", "submit_cta"}


@pytest.mark.asyncio
async def test_supplier_gateway_rejects_mismatched_reply():
    class Context:
        async def send_and_receive(self, destination, message, **kwargs):
            from types import SimpleNamespace

            from uagents_core.types import DeliveryStatus

            return SupplierToolResult(
                request_id=str(uuid4()),
                correlation_id=message.correlation_id,
                supplier_id=message.supplier_id,
                operation=message.operation,
                success=True,
            ), SimpleNamespace(status=DeliveryStatus.DELIVERED)

    with pytest.raises(ValueError, match="does not match"):
        await SupplierAgentClient(Context(), "agent1supplier")._call(
            "capacity", "supplier-alpha", str(uuid4()), "{}"
        )
