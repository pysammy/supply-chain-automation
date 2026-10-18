"""Payment receipts live with the existing durable workflow; no local agent storage."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from supplymesh.persistence.orm_models import AuditEventRecord, WorkflowRun


class PaymentState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_id: UUID
    conversation_id: str
    checkout_session_id: str
    amount_minor: int = Field(gt=0)
    currency: str = Field(pattern=r"^(usd|eur)$")
    status: Literal["PENDING", "PAID"] = "PENDING"
    paid_at: str | None = None


class PaymentRepository:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    def get(self, workflow_id: UUID, conversation_id: str) -> PaymentState | None:
        with self.session_factory() as session:
            workflow = session.get(WorkflowRun, workflow_id)
            if workflow is None or workflow.conversation_id != conversation_id:
                raise ValueError("workflow does not belong to this conversation")
            if workflow.status != "COMPLETED":
                raise ValueError("only completed procurement workflows can be paid")
            value = workflow.state.get("payment")
            return PaymentState.model_validate(value) if value else None

    def find_session(self, conversation_id: str, session_id: str) -> PaymentState | None:
        with self.session_factory() as session:
            workflow = session.scalar(
                select(WorkflowRun).where(
                    WorkflowRun.conversation_id == conversation_id,
                    WorkflowRun.state["payment"]["checkout_session_id"].as_string() == session_id,
                )
            )
            if workflow is None or workflow.status != "COMPLETED":
                return None
            return PaymentState.model_validate(workflow.state["payment"])

    def save_pending(self, payment: PaymentState) -> PaymentState:
        with self.session_factory() as session, session.begin():
            workflow = session.scalar(
                select(WorkflowRun).where(WorkflowRun.id == payment.workflow_id).with_for_update()
            )
            if (
                workflow is None
                or workflow.conversation_id != payment.conversation_id
                or workflow.status != "COMPLETED"
            ):
                raise ValueError("payment requires this conversation's completed workflow")
            existing = workflow.state.get("payment")
            if existing:
                saved = PaymentState.model_validate(existing)
                if (
                    saved.checkout_session_id != payment.checkout_session_id
                    or saved.amount_minor != payment.amount_minor
                    or saved.currency != payment.currency
                ):
                    raise ValueError("workflow already has a different checkout")
                return saved
            workflow.state = {**workflow.state, "payment": payment.model_dump(mode="json")}
            workflow.checkpoint_version += 1
            return payment

    def mark_paid(self, payment: PaymentState) -> PaymentState:
        """Persist the receipt and audit event atomically, before acknowledging the buyer."""
        with self.session_factory() as session, session.begin():
            workflow = session.scalar(
                select(WorkflowRun).where(WorkflowRun.id == payment.workflow_id).with_for_update()
            )
            if (
                workflow is None
                or workflow.conversation_id != payment.conversation_id
                or workflow.status != "COMPLETED"
            ):
                raise ValueError("payment workflow is unavailable")
            current = PaymentState.model_validate(workflow.state.get("payment"))
            if (
                current.checkout_session_id != payment.checkout_session_id
                or current.amount_minor != payment.amount_minor
                or current.currency != payment.currency
            ):
                raise ValueError("payment session does not match the workflow")
            if current.status == "PAID":
                return current
            paid = current.model_copy(
                update={"status": "PAID", "paid_at": datetime.now(UTC).isoformat()}
            )
            workflow.state = {**workflow.state, "payment": paid.model_dump(mode="json")}
            workflow.checkpoint_version += 1
            session.add(
                AuditEventRecord(
                    id=uuid5(payment.workflow_id, f"stripe-receipt:{payment.checkout_session_id}"),
                    workflow_id=payment.workflow_id,
                    actor_type="HUMAN",
                    actor_id=payment.conversation_id,
                    action="PAYMENT_COLLECTED",
                    entity_type="payment",
                    entity_id=payment.checkout_session_id,
                    before_json={"status": "PENDING"},
                    after_json={
                        "status": "PAID",
                        "amount_minor": payment.amount_minor,
                        "currency": payment.currency,
                    },
                )
            )
            return paid
