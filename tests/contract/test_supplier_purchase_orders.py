from datetime import date
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.supplier_simulator.main import create_app
from supplymesh.domain.procurement import PurchaseOrder, PurchaseOrderLine
from supplymesh.edi.x12_850 import generate_850
from supplymesh.edi.x12_855 import parse_855


def po_payload() -> dict[str, object]:
    po = PurchaseOrder(
        po_id=uuid4(),
        po_number="PO-2026-0002",
        workflow_id=uuid4(),
        supplier_id="supplier-alpha",
        version=1,
        currency="USD",
        lines=(
            PurchaseOrderLine(
                line_number=1,
                component_sku="ENGINE-001",
                quantity=Decimal("75"),
                unit_price=Decimal("820.00"),
                requested_date=date(2026, 12, 1),
            ),
        ),
        total=Decimal("61500.00"),
    )
    return {
        "message_id": str(uuid4()),
        "document_type": "850",
        "po_number": po.po_number,
        "po_version": 1,
        "x12": generate_850(po, "100"),
    }


def test_po_submission_is_idempotent_and_returns_parseable_855() -> None:
    client = TestClient(create_app(api_key="test-key", admin_api_key="admin-key"))
    headers = {
        "X-API-Key": "test-key",
        "Idempotency-Key": "purchase-order-key-000001",
        "X-Correlation-ID": str(uuid4()),
    }
    payload = po_payload()
    first = client.post(
        "/api/v1/suppliers/supplier-alpha/purchase-orders", json=payload, headers=headers
    )
    second = client.post(
        "/api/v1/suppliers/supplier-alpha/purchase-orders", json=payload, headers=headers
    )
    assert first.status_code == 202
    assert first.json() == second.json()
    status = client.get(
        first.json()["poll_url"],
        headers={"X-API-Key": "test-key", "X-Correlation-ID": str(uuid4())},
    )
    assert status.json()["status"] == "ACKNOWLEDGED"
    assert parse_855(status.json()["x12"]).po_number == payload["po_number"]
