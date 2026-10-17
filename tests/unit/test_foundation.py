import json
import logging
from datetime import UTC
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from apps.agent_host.health import app as agent_app
from apps.supplier_simulator.main import create_app
from supplymesh.observability.logging import JsonFormatter
from supplymesh.protocols.common import MessageEnvelope


def test_agent_health() -> None:
    response = TestClient(agent_app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "agent-host"}


def test_agent_readiness_returns_503_when_database_is_unavailable(monkeypatch) -> None:
    class UnavailableEngine:
        def connect(self) -> None:
            raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr("apps.agent_host.health.get_engine", lambda: UnavailableEngine())
    response = TestClient(agent_app).get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "reason": "SQLAlchemyError"}


def test_supplier_health_and_readiness() -> None:
    client = TestClient(create_app(api_key="test-api-key", admin_api_key="test-admin-key"))
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/ready").json() == {
        "status": "ready",
        "service": "supplier-simulator",
    }
    unconfigured = TestClient(create_app())
    response = unconfigured.get("/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_message_envelope_has_utc_identity_fields() -> None:
    envelope = MessageEnvelope(
        correlation_id=uuid4(), workflow_id=uuid4(), sender_agent="orchestrator"
    )
    assert envelope.schema_version == "1.0"
    assert envelope.sent_at.tzinfo == UTC
    assert envelope.message_id


def test_json_logging_redacts_credentials() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Authorization: Bearer abc.def X-API-Key=super-secret AGENT_SEED=seed-value",
        args=(),
        exc_info=None,
    )
    payload = json.loads(JsonFormatter().format(record))

    assert "abc.def" not in payload["message"]
    assert "super-secret" not in payload["message"]
    assert "seed-value" not in payload["message"]
    assert payload["message"].count("[REDACTED]") >= 3
