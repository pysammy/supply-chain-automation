from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from supplymesh.persistence.audit_repository import AuditRepository
from supplymesh.persistence.orm_models import (
    AgentMessageLogRecord,
    ApprovalDecisionRecord,
    AuditEventRecord,
    WorkflowRun,
)
from supplymesh.persistence.workflow_repository import ConnectivitySession, WorkflowRepository


def test_runtime_audit_is_idempotent_and_tracks_workflow_and_approval() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in (
        WorkflowRun.__table__,
        ApprovalDecisionRecord.__table__,
        AgentMessageLogRecord.__table__,
        AuditEventRecord.__table__,
    ):
        table.create(engine)

    def sessions() -> Session:
        return Session(engine)

    audit = AuditRepository(sessions)
    workflows = WorkflowRepository(sessions, audit)
    workflow_id = uuid4()
    message_id = uuid4()
    workflows.start_inventory(
        ConnectivitySession(
            workflow_id=workflow_id,
            conversation_id="agent1manager",
            correlation_id=uuid4(),
            request_message_id=uuid4(),
            chat_message_id=uuid4(),
            status="INVENTORY_PLANNING",
        )
    )
    assert workflows.update(
        workflow_id,
        status="AWAITING_PO_APPROVAL",
        updates={"purchase_order_id": "po-1"},
        inbound_message_id=uuid4(),
    )
    approval = workflows.save_approval(
        workflow_id=workflow_id,
        decision_type="SEND_PURCHASE_ORDER",
        target_type="purchase_order",
        target_id="po-1",
        target_version=1,
        actor="manager@example.com",
        payload={"po_id": "po-1", "version": 1},
    )

    sent_at = datetime(2026, 10, 16, tzinfo=UTC)
    audit.log_message(
        workflow_id=workflow_id,
        message_id=message_id,
        sender="orchestrator",
        recipient="procurement",
        message_type="PurchaseOrderExecutionRequest",
        payload_hash="a" * 64,
        status="PENDING",
        sent_at=sent_at,
    )
    audit.log_message(
        workflow_id=workflow_id,
        message_id=message_id,
        sender="orchestrator",
        recipient="procurement",
        message_type="PurchaseOrderExecutionRequest",
        payload_hash="a" * 64,
        status="DELIVERED",
        sent_at=sent_at,
        received_at=sent_at,
    )

    with sessions() as session:
        messages = tuple(session.scalars(select(AgentMessageLogRecord)))
        events = tuple(
            session.scalars(select(AuditEventRecord).order_by(AuditEventRecord.created_at))
        )
        approvals = tuple(session.scalars(select(ApprovalDecisionRecord)))

    assert len(messages) == 1
    assert messages[0].status == "DELIVERED"
    assert messages[0].received_at == sent_at.replace(tzinfo=None)
    assert [event.action for event in events] == [
        "WORKFLOW_STARTED",
        "WORKFLOW_UPDATED",
        "SEND_PURCHASE_ORDER",
    ]
    assert events[1].before_json is not None
    assert events[1].before_json["status"] == "INVENTORY_PLANNING"
    assert events[1].after_json is not None
    assert events[1].after_json["status"] == "AWAITING_PO_APPROVAL"
    assert len(approvals) == 1
    assert approvals[0].id == approval.approval_id
    activity = audit.list_activity(workflow_id)
    assert {item["kind"] for item in activity} == {"event", "message"}
    assert any(
        item["action"] == "PurchaseOrderExecutionRequest" and item["outcome"] == "DELIVERED"
        for item in activity
    )
    assert any(item["action"] == "SEND_PURCHASE_ORDER" for item in activity)
    engine.dispose()
