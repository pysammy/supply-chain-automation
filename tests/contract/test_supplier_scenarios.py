from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.supplier_simulator.main import create_app


def test_first_call_failure_scenario_recovers_and_admin_auth_is_required() -> None:
    client = TestClient(create_app(api_key="api", admin_api_key="admin"))
    assert (
        client.post(
            "/api/v1/demo/scenario/first_call_timeout_then_success",
            headers={"X-API-Key": "bad"},
        ).status_code
        == 401
    )
    configured = client.post(
        "/api/v1/demo/scenario/first_call_timeout_then_success",
        headers={"X-API-Key": "admin"},
    )
    assert configured.status_code == 200
    payload = {
        "rfq_id": str(uuid4()),
        "currency": "USD",
        "valid_until": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "lines": [
            {
                "line_number": 1,
                "component_sku": "ENGINE-001",
                "quantity": "10",
                "required_by": "2026-12-01",
            }
        ],
    }
    headers = {
        "X-API-Key": "api",
        "Idempotency-Key": "scenario-rfq-key-00001",
        "X-Correlation-ID": str(uuid4()),
    }
    path = "/api/v1/suppliers/supplier-alpha/rfqs"
    assert client.post(path, json=payload, headers=headers).status_code == 503
    assert client.post(path, json=payload, headers=headers).status_code == 200
