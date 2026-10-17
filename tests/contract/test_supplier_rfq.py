from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.supplier_simulator.main import create_app


def request_payload() -> dict[str, object]:
    return {
        "rfq_id": str(uuid4()),
        "currency": "USD",
        "valid_until": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "lines": [
            {
                "line_number": 1,
                "component_sku": "ENGINE-001",
                "quantity": "75",
                "required_by": "2026-12-01",
            }
        ],
    }


def headers(key: str = "test-key") -> dict[str, str]:
    return {
        "X-API-Key": key,
        "Idempotency-Key": "rfq-idempotency-key-0001",
        "X-Correlation-ID": str(uuid4()),
    }


def test_three_supplier_profiles_return_distinct_rest_quotes() -> None:
    client = TestClient(create_app(api_key="test-key", admin_api_key="admin-key"))
    responses = [
        client.post(f"/api/v1/suppliers/{supplier}/rfqs", json=request_payload(), headers=headers())
        for supplier in ("supplier-alpha", "supplier-beta", "supplier-gamma")
    ]
    assert all(response.status_code == 200 for response in responses)
    prices = {response.json()["lines"][0]["unit_price"] for response in responses}
    currencies = {response.json()["currency"] for response in responses}
    assert len(prices) == 3
    assert currencies == {"USD", "EUR"}


def test_rfq_idempotency_returns_original_response() -> None:
    client = TestClient(create_app(api_key="test-key", admin_api_key="admin-key"))
    first = client.post(
        "/api/v1/suppliers/supplier-alpha/rfqs", json=request_payload(), headers=headers()
    )
    second = client.post(
        "/api/v1/suppliers/supplier-alpha/rfqs", json=request_payload(), headers=headers()
    )
    assert first.json() == second.json()


def test_authentication_and_required_correlation_headers() -> None:
    client = TestClient(create_app(api_key="test-key", admin_api_key="admin-key"))
    assert (
        client.post(
            "/api/v1/suppliers/supplier-alpha/rfqs", json=request_payload(), headers=headers("bad")
        ).status_code
        == 401
    )
    incomplete = headers()
    incomplete.pop("X-Correlation-ID")
    assert (
        client.post(
            "/api/v1/suppliers/supplier-alpha/rfqs", json=request_payload(), headers=incomplete
        ).status_code
        == 422
    )
