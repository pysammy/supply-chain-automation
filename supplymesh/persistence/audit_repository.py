import json
from collections.abc import Callable
from datetime import datetime
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from supplymesh.persistence.orm_models import AgentMessageLogRecord, AuditEventRecord


class AuditRepository:
    """Append-only message and material business-event audit writes."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    def log_message(
        self,
        *,
        workflow_id: UUID,
        message_id: UUID,
        sender: str,
        recipient: str,
        message_type: str,
        payload_hash: str,
        status: str,
        sent_at: datetime,
        received_at: datetime | None = None,
    ) -> UUID:
        with self.session_factory() as session, session.begin():
            existing = session.scalar(
                select(AgentMessageLogRecord).where(AgentMessageLogRecord.message_id == message_id)
            )
            if existing is not None:
                immutable = (
                    existing.workflow_id,
                    existing.sender,
                    existing.recipient,
                    existing.message_type,
                    existing.payload_hash,
                )
                incoming = (workflow_id, sender, recipient, message_type, payload_hash)
                if immutable != incoming:
                    raise ValueError("message ID is already bound to different audit content")
                existing.status = status
                existing.received_at = received_at
                return existing.id
            record_id = uuid5(workflow_id, f"message:{message_id}")
            session.add(
                AgentMessageLogRecord(
                    id=record_id,
                    workflow_id=workflow_id,
                    message_id=message_id,
                    sender=sender,
                    recipient=recipient,
                    message_type=message_type,
                    payload_hash=payload_hash,
                    status=status,
                    sent_at=sent_at,
                    received_at=received_at,
                )
            )
            return record_id

    def append_event(
        self,
        *,
        workflow_id: UUID,
        actor_type: str,
        actor_id: str,
        action: str,
        entity_type: str,
        entity_id: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> UUID:
        material = json.dumps(
            {
                "actor_type": actor_type,
                "actor_id": actor_id,
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "before": before,
                "after": after,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        event_id = uuid5(workflow_id, f"event:{sha256(material.encode()).hexdigest()}")
        with self.session_factory() as session, session.begin():
            if session.get(AuditEventRecord, event_id) is not None:
                return event_id
            session.add(
                AuditEventRecord(
                    id=event_id,
                    workflow_id=workflow_id,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    action=action,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    before_json=before,
                    after_json=after,
                )
            )
        return event_id

    def list_activity(self, workflow_id: UUID, *, limit: int = 50) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            messages = tuple(
                session.scalars(
                    select(AgentMessageLogRecord)
                    .where(AgentMessageLogRecord.workflow_id == workflow_id)
                    .order_by(AgentMessageLogRecord.sent_at)
                )
            )
            events = tuple(
                session.scalars(
                    select(AuditEventRecord)
                    .where(AuditEventRecord.workflow_id == workflow_id)
                    .order_by(AuditEventRecord.created_at)
                )
            )
        activity = [
            {
                "kind": "message",
                "agent": message.sender,
                "action": message.message_type,
                "outcome": message.status,
                "recipient": message.recipient,
                "timestamp": message.sent_at.isoformat(),
            }
            for message in messages
        ]
        activity.extend(
            {
                "kind": "event",
                "agent": event.actor_id,
                "action": event.action,
                "outcome": (event.after_json or {}).get("status", "RECORDED"),
                "entity": event.entity_type,
                "timestamp": event.created_at.isoformat(),
            }
            for event in events
        )
        activity.sort(key=lambda item: (str(item["timestamp"]), str(item["action"])))
        return activity[-limit:]
