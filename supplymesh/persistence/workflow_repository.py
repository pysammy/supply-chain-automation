from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from supplymesh.domain.procurement import ApprovalRecord
from supplymesh.persistence.audit_repository import AuditRepository
from supplymesh.persistence.orm_models import ApprovalDecisionRecord, WorkflowRun
from supplymesh.services.approval_service import approval_payload_hash


@dataclass(frozen=True)
class ConnectivitySession:
    workflow_id: UUID
    conversation_id: str
    correlation_id: UUID
    request_message_id: UUID
    chat_message_id: UUID
    status: str
    currency: str = "USD"


@dataclass(frozen=True)
class WorkflowSnapshot:
    workflow_id: UUID
    conversation_id: str
    status: str
    checkpoint_version: int
    state: dict[str, Any]


@dataclass(frozen=True)
class PendingOutbound:
    workflow_id: UUID
    message_id: UUID
    destination: str
    message_type: str
    payload_json: str


class WorkflowRepository:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        audit: AuditRepository | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.audit = audit

    def start_inventory(self, session_state: ConnectivitySession) -> None:
        with self.session_factory() as session, session.begin():
            session.add(
                WorkflowRun(
                    id=session_state.workflow_id,
                    conversation_id=session_state.conversation_id,
                    status=session_state.status,
                    checkpoint_version=1,
                    state=self._state(session_state),
                )
            )
        if self.audit is not None:
            self.audit.append_event(
                workflow_id=session_state.workflow_id,
                actor_type="AGENT",
                actor_id="manufacturing-orchestrator",
                action="WORKFLOW_STARTED",
                entity_type="workflow",
                entity_id=str(session_state.workflow_id),
                before=None,
                after={"status": session_state.status},
            )

    def get(self, workflow_id: UUID) -> ConnectivitySession | None:
        with self.session_factory() as session:
            record = session.get(WorkflowRun, workflow_id)
            if record is None:
                return None
            return self._session(record)

    def find_by_chat_message(self, chat_message_id: UUID) -> ConnectivitySession | None:
        with self.session_factory() as session:
            record = session.scalar(
                select(WorkflowRun).where(
                    WorkflowRun.state["chat_message_id"].as_string() == str(chat_message_id)
                )
            )
            return self._session(record) if record is not None else None

    def find_active_by_conversation(self, conversation_id: str) -> WorkflowSnapshot | None:
        terminal = ("COMPLETED", "CANCELLED", "FAILED_REQUIRES_OPERATOR")
        with self.session_factory() as session:
            record = session.scalar(
                select(WorkflowRun)
                .where(
                    WorkflowRun.conversation_id == conversation_id,
                    WorkflowRun.status.not_in(terminal),
                )
                .order_by(WorkflowRun.started_at.desc())
                .limit(1)
            )
            return self._snapshot(record) if record is not None else None

    def snapshot(self, workflow_id: UUID) -> WorkflowSnapshot | None:
        with self.session_factory() as session:
            record = session.get(WorkflowRun, workflow_id)
            return self._snapshot(record) if record is not None else None

    def update(
        self,
        workflow_id: UUID,
        *,
        status: str | None = None,
        updates: dict[str, Any] | None = None,
        inbound_message_id: UUID | None = None,
    ) -> bool:
        """Atomically deduplicate an inbound message and persist its resulting state."""
        before: dict[str, Any] | None = None
        after: dict[str, Any] | None = None
        with self.session_factory() as session, session.begin():
            record = session.get(WorkflowRun, workflow_id, with_for_update=True)
            if record is None:
                raise ValueError("workflow was not found")
            state = dict(record.state)
            processed = list(state.get("processed_message_ids", []))
            if inbound_message_id is not None and str(inbound_message_id) in processed:
                return False
            before = {"status": record.status, "state": state}
            if inbound_message_id is not None:
                processed.append(str(inbound_message_id))
                state["processed_message_ids"] = processed
            if updates:
                state.update(updates)
            record.state = state
            if status is not None:
                record.status = status
                if status in {"COMPLETED", "CANCELLED", "FAILED_REQUIRES_OPERATOR"}:
                    record.completed_at = datetime.now(UTC)
            record.checkpoint_version += 1
            after = {"status": record.status, "state": state}
        if self.audit is not None:
            self.audit.append_event(
                workflow_id=workflow_id,
                actor_type="AGENT",
                actor_id="manufacturing-orchestrator",
                action="WORKFLOW_UPDATED",
                entity_type="workflow",
                entity_id=str(workflow_id),
                before=before,
                after=after,
            )
        return True

    def record_outbound(
        self,
        workflow_id: UUID,
        *,
        message_id: UUID,
        destination: str,
        message_type: str,
        status: str,
        payload_json: str | None = None,
    ) -> None:
        with self.session_factory() as session, session.begin():
            record = session.get(WorkflowRun, workflow_id, with_for_update=True)
            if record is None:
                raise ValueError("workflow was not found")
            state = dict(record.state)
            outbound = dict(state.get("outbound_messages", {}))
            current = outbound.get(str(message_id))
            if current is not None and (
                current["destination"] != destination or current["message_type"] != message_type
            ):
                raise ValueError("message ID is already bound to a different outbound message")
            if (
                current is not None
                and payload_json is not None
                and current.get("payload_json") not in {None, payload_json}
            ):
                raise ValueError("message ID is already bound to a different outbound payload")
            outbound[str(message_id)] = {
                "destination": destination,
                "message_type": message_type,
                "status": status,
                "payload_json": payload_json or (current or {}).get("payload_json"),
            }
            state["outbound_messages"] = outbound
            record.state = state
            record.checkpoint_version += 1

    def pending_outbound(self) -> tuple[PendingOutbound, ...]:
        with self.session_factory() as session:
            records = tuple(session.scalars(select(WorkflowRun).order_by(WorkflowRun.started_at)))
            pending: list[PendingOutbound] = []
            for record in records:
                for message_id, value in record.state.get("outbound_messages", {}).items():
                    if value.get("status") != "PENDING" or not value.get("payload_json"):
                        continue
                    pending.append(
                        PendingOutbound(
                            workflow_id=record.id,
                            message_id=UUID(message_id),
                            destination=str(value["destination"]),
                            message_type=str(value["message_type"]),
                            payload_json=str(value["payload_json"]),
                        )
                    )
            return tuple(pending)

    def save_approval(
        self,
        *,
        workflow_id: UUID,
        decision_type: Literal[
            "SELECT_PLAN", "SEND_PURCHASE_ORDER", "ACCEPT_EXCEPTION", "SEND_COUNTERPROPOSAL"
        ],
        target_type: str,
        target_id: str,
        target_version: int,
        actor: str,
        payload: BaseModel | dict[str, Any],
        reason: str | None = None,
    ) -> ApprovalRecord:
        payload_hash = approval_payload_hash(payload)
        approval_id = uuid5(
            workflow_id,
            f"{decision_type}:{target_id}:{target_version}:{actor}:{payload_hash}",
        )
        approval = ApprovalRecord(
            approval_id=approval_id,
            workflow_id=workflow_id,
            decision_type=decision_type,
            target_type=target_type,
            target_id=target_id,
            target_version=target_version,
            decision="APPROVED",
            actor=actor,
            reason=reason,
            payload_hash=payload_hash,
            decided_at=datetime.now(UTC),
        )
        with self.session_factory() as session, session.begin():
            existing = session.get(ApprovalDecisionRecord, approval_id)
            if existing is None:
                session.add(
                    ApprovalDecisionRecord(
                        id=approval.approval_id,
                        workflow_id=approval.workflow_id,
                        decision_type=approval.decision_type,
                        target_type=approval.target_type,
                        target_id=approval.target_id,
                        target_version=approval.target_version,
                        decision=approval.decision,
                        actor=approval.actor,
                        reason=approval.reason,
                        payload_hash=approval.payload_hash,
                        decided_at=approval.decided_at,
                    )
                )
        if self.audit is not None:
            self.audit.append_event(
                workflow_id=workflow_id,
                actor_type="HUMAN",
                actor_id=actor,
                action=decision_type,
                entity_type=target_type,
                entity_id=target_id,
                before=None,
                after={
                    "approval_id": str(approval.approval_id),
                    "target_version": target_version,
                    "decision": approval.decision,
                    "payload_hash": payload_hash,
                },
            )
        return approval

    def record_inventory_result(self, workflow_id: UUID, result: dict[str, Any]) -> None:
        before: dict[str, Any] | None = None
        after: dict[str, Any] | None = None
        with self.session_factory() as session, session.begin():
            record = session.get(WorkflowRun, workflow_id)
            if record is None:
                raise ValueError("inventory result references an unknown workflow")
            state = dict(record.state)
            before = {"status": record.status, "state": state}
            state["inventory_result"] = result
            record.state = state
            record.status = "SOURCING" if result["has_shortages"] else "COMPLETED"
            record.checkpoint_version += 1
            after = {"status": record.status, "state": state}
        if self.audit is not None:
            self.audit.append_event(
                workflow_id=workflow_id,
                actor_type="AGENT",
                actor_id="inventory-and-bom",
                action="INVENTORY_RESULT_RECORDED",
                entity_type="workflow",
                entity_id=str(workflow_id),
                before=before,
                after=after,
            )

    def record_failure(self, workflow_id: UUID, error: dict[str, Any]) -> None:
        before: dict[str, Any] | None = None
        after: dict[str, Any] | None = None
        with self.session_factory() as session, session.begin():
            record = session.get(WorkflowRun, workflow_id)
            if record is None:
                return
            state = dict(record.state)
            before = {"status": record.status, "state": state}
            state["error"] = error
            record.state = state
            record.status = "FAILED_REQUIRES_OPERATOR"
            record.checkpoint_version += 1
            after = {"status": record.status, "state": state}
        if self.audit is not None:
            self.audit.append_event(
                workflow_id=workflow_id,
                actor_type="SYSTEM",
                actor_id="manufacturing-orchestrator",
                action="WORKFLOW_FAILED",
                entity_type="workflow",
                entity_id=str(workflow_id),
                before=before,
                after=after,
            )

    @staticmethod
    def _state(session_state: ConnectivitySession) -> dict[str, str]:
        return {
            "correlation_id": str(session_state.correlation_id),
            "request_message_id": str(session_state.request_message_id),
            "chat_message_id": str(session_state.chat_message_id),
            "currency": session_state.currency,
        }

    @staticmethod
    def _session(record: WorkflowRun) -> ConnectivitySession:
        state = record.state
        return ConnectivitySession(
            workflow_id=record.id,
            conversation_id=record.conversation_id,
            correlation_id=UUID(state["correlation_id"]),
            request_message_id=UUID(state["request_message_id"]),
            chat_message_id=UUID(state["chat_message_id"]),
            status=record.status,
            currency=state.get("currency", "USD"),
        )

    @staticmethod
    def _snapshot(record: WorkflowRun) -> WorkflowSnapshot:
        return WorkflowSnapshot(
            workflow_id=record.id,
            conversation_id=record.conversation_id,
            status=record.status,
            checkpoint_version=record.checkpoint_version,
            state=dict(record.state),
        )
