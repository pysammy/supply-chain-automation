from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from apps.supplier_simulator.main import create_app
from supplymesh.agents.sourcing import SourcingAgentService
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
from supplymesh.services.comparison_service import compare_acknowledgment
from supplymesh.services.counterproposal_service import execute_counterproposal
from supplymesh.services.procurement_service import execute_purchase_order


class Journal:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, str]] = {}

    def find_success(self, key: str) -> dict[str, str] | None:
        return self.values.get(key)

    def record_pending(self, key: str, operation: str, payload_hash: str) -> None:
        del key, operation, payload_hash

    def record_success(self, key: str, result: dict[str, str]) -> None:
        self.values[key] = result


def approval(
    po: PurchaseOrder, decision_type: str, payload: PurchaseOrder | Counterproposal
) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=uuid4(),
        workflow_id=po.workflow_id,
        decision_type=decision_type,
        target_type="purchase_order",
        target_id=str(po.po_id),
        target_version=po.version,
        decision="APPROVED",
        actor="e2e-manager",
        payload_hash=approval_payload_hash(payload),
        decided_at=datetime(2026, 10, 16, tzinfo=UTC),
    )


async def set_scenario(app, name: str) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/v1/demo/scenario/{name}", headers={"X-API-Key": "admin"}
        )
        response.raise_for_status()


@pytest.mark.asyncio
async def test_shortage_quotes_optimization_approval_po_and_exact_ack_complete() -> None:
    app = create_app(api_key="api", admin_api_key="admin")
    await set_scenario(app, "happy_path")
    client = SupplierClient(
        base_url="http://test",
        api_key="api",
        transport=httpx.ASGITransport(app=app),
        max_retries=0,
    )
    workflow_id = uuid4()
    request = SourcingRequest(
        correlation_id=uuid4(),
        workflow_id=workflow_id,
        sender_agent="orchestrator",
        shortages={"ENGINE-001": 75},
        required_by={"ENGINE-001": "2026-12-01"},
    )
    sourcing = SourcingAgentService(
        client,
        ("supplier-alpha", "supplier-beta", "supplier-gamma"),
        minimum_valid_quotes=2,
    )
    collected = await sourcing.collect(request, now=datetime(2026, 10, 16, tzinfo=UTC))
    ready = sourcing.plan(
        request, collected, fx_to_target={"USD": Decimal("1"), "EUR": Decimal("1.10")}
    )
    plan = next(item for item in ready.result.plans if item.name == "cheapest")
    allocation = plan.allocations[0]
    po = PurchaseOrder(
        po_id=uuid4(),
        po_number="PO-E2E-1",
        workflow_id=workflow_id,
        supplier_id=allocation.supplier_id,
        version=1,
        currency="USD",
        lines=(
            PurchaseOrderLine(
                line_number=1,
                component_sku=allocation.component_sku,
                quantity=Decimal(allocation.quantity),
                unit_price=Decimal(allocation.unit_price_minor) / 100,
                requested_date=allocation.promised_date,
            ),
        ),
        total=Decimal(plan.total_cost_minor) / 100,
    )
    accepted = await execute_purchase_order(
        po,
        approval(po, "SEND_PURCHASE_ORDER", po),
        client,
        Journal(),
        correlation_id=str(request.correlation_id),
    )
    status = await client.get_purchase_order_status(
        po.supplier_id,
        accepted.supplier_transaction_id,
        correlation_id=str(request.correlation_id),
    )
    comparison = compare_acknowledgment(po, parse_855(status.x12 or ""))
    assert comparison.status == "MATCHED"
    assert comparison.recommended_action == "complete"


@pytest.mark.asyncio
async def test_price_conflict_human_counterproposal_and_accepted_865() -> None:
    app = create_app(api_key="api", admin_api_key="admin")
    await set_scenario(app, "price_conflict")
    client = SupplierClient(
        base_url="http://test", api_key="api", transport=httpx.ASGITransport(app=app)
    )
    po = PurchaseOrder(
        po_id=uuid4(),
        po_number="PO-E2E-2",
        workflow_id=uuid4(),
        supplier_id="supplier-beta",
        version=1,
        currency="USD",
        lines=(
            PurchaseOrderLine(
                line_number=1,
                component_sku="ENGINE-001",
                quantity=Decimal("75"),
                unit_price=Decimal("875"),
                requested_date=date(2026, 12, 1),
            ),
        ),
        total=Decimal("65625"),
    )
    transaction = await execute_purchase_order(
        po,
        approval(po, "SEND_PURCHASE_ORDER", po),
        client,
        Journal(),
        correlation_id=str(uuid4()),
    )
    status = await client.get_purchase_order_status(
        po.supplier_id, transaction.supplier_transaction_id, correlation_id=str(uuid4())
    )
    comparison = compare_acknowledgment(po, parse_855(status.x12 or ""))
    assert comparison.status == "EXCEPTION"
    assert comparison.recommended_action == "counter"
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
                quantity=Decimal("75"),
                unit="EA",
                maximum_unit_price=Decimal("880"),
                requested_date=date(2026, 12, 1),
            ),
        ),
    )
    change = await execute_counterproposal(
        counter,
        approval(po, "SEND_COUNTERPROPOSAL", counter),
        client,
        Journal(),
        correlation_id=str(uuid4()),
    )
    response = await client.get_change_order_status(
        po.supplier_id, change.supplier_transaction_id, correlation_id=str(uuid4())
    )
    parsed = parse_865(response.x12 or "")
    assert parsed.po_version == 2
    assert parsed.lines[0].response_code == "IA"


@pytest.mark.asyncio
async def test_real_supplier_boundary_all_failures_pause_without_inventing_quotes() -> None:
    app = create_app(api_key="api", admin_api_key="admin")
    await set_scenario(app, "supplier_unavailable")
    client = SupplierClient(
        base_url="http://test",
        api_key="api",
        transport=httpx.ASGITransport(app=app),
        max_retries=0,
    )
    request = SourcingRequest(
        correlation_id=uuid4(),
        workflow_id=uuid4(),
        sender_agent="orchestrator",
        shortages={"ENGINE-001": 75},
        required_by={"ENGINE-001": "2026-12-01"},
    )
    sourcing = SourcingAgentService(
        client,
        ("supplier-alpha", "supplier-beta", "supplier-gamma"),
        minimum_valid_quotes=2,
    )

    with pytest.raises(RuntimeError, match="only 0 valid supplier quotes"):
        await sourcing.collect(request, now=datetime(2026, 10, 16, tzinfo=UTC))
