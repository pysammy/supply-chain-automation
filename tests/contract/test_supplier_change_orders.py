from datetime import date
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.supplier_simulator.main import create_app
from supplymesh.edi.x12_860 import ChangeRequestLine, generate_860
from supplymesh.edi.x12_865 import parse_865


def test_counterproposal_is_idempotent_and_supplier_returns_accepted_865() -> None:
    client = TestClient(create_app(api_key="test-key", admin_api_key="admin-key"))
    payload = {
        "message_id": str(uuid4()),
        "document_type": "860",
        "po_number": "PO-1",
        "po_version": 2,
        "x12": generate_860(
            po_number="PO-1",
            po_version=2,
            currency="USD",
            lines=(
                ChangeRequestLine(
                    line_number=1,
                    component_sku="ENGINE-001",
                    quantity=Decimal("75"),
                    unit="EA",
                    maximum_unit_price=Decimal("825"),
                    requested_date=date(2026, 12, 1),
                ),
            ),
            control_number="99",
        ),
    }
    headers = {
        "X-API-Key": "test-key",
        "Idempotency-Key": "change-order-key-000001",
        "X-Correlation-ID": str(uuid4()),
    }
    first = client.post(
        "/api/v1/suppliers/supplier-alpha/change-orders", json=payload, headers=headers
    )
    second = client.post(
        "/api/v1/suppliers/supplier-alpha/change-orders", json=payload, headers=headers
    )
    assert first.status_code == 202
    assert first.json() == second.json()
    status = client.get(
        first.json()["poll_url"],
        headers={"X-API-Key": "test-key", "X-Correlation-ID": str(uuid4())},
    )
    parsed = parse_865(status.json()["x12"])
    assert parsed.lines[0].response_code == "IA"
