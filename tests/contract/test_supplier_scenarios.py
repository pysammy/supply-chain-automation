import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.supplier_simulator.main import create_app
from supplymesh.domain.sourcing import SupplierQuote
from supplymesh.services.optimizer import optimize_plans
from supplymesh.services.sourcing_service import normalize_quotes


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


def test_catalog_quotes_cover_full_demo_bom_and_produce_feasible_plans() -> None:
    bom_path = Path(__file__).resolve().parents[2] / "data" / "motorcycle_bom.json"
    components = json.loads(bom_path.read_text())["components"]
    required_by = (datetime.now(UTC) + timedelta(days=60)).date().isoformat()
    shortages = {item["component_sku"]: 20 for item in components}
    payload = {
        "rfq_id": str(uuid4()),
        "currency": "USD",
        "valid_until": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "lines": [
            {
                "line_number": index,
                "component_sku": sku,
                "quantity": "20",
                "required_by": required_by,
            }
            for index, sku in enumerate(shortages, start=1)
        ],
    }
    client = TestClient(create_app(api_key="api", admin_api_key="admin"))
    quotes = []
    for supplier in ("supplier-alpha", "supplier-beta", "supplier-gamma"):
        response = client.post(
            f"/api/v1/suppliers/{supplier}/rfqs",
            json=payload,
            headers={
                "X-API-Key": "api",
                "Idempotency-Key": f"full-bom-{supplier}",
                "X-Correlation-ID": str(uuid4()),
            },
        )
        assert response.status_code == 200
        quotes.append(SupplierQuote.model_validate(response.json()))
    lines = normalize_quotes(
        tuple(quotes),
        dict.fromkeys(shortages, required_by),
        {"USD": Decimal("1"), "EUR": Decimal("1.1")},
    )
    result = optimize_plans(shortages, lines)
    assert not result.infeasibility_reasons
    assert len(result.plans) == 5
    for plan in result.plans:
        for sku, needed in shortages.items():
            supplied = sum(item.quantity for item in plan.allocations if item.component_sku == sku)
            assert supplied >= needed, (plan.name, sku)
