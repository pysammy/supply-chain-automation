import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from supplymesh.agents.procurement import ProcurementAgentService, build_procurement_protocol
from supplymesh.agents.transport import (
    AcknowledgmentReceivedTransport,
    PurchaseOrderSubmittedTransport,
)
from supplymesh.domain.procurement import (
    ApprovalRecord,
    ChangeOrderSubmission,
    ChangeTransactionStatus,
    Counterproposal,
    CounterproposalLine,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderSubmission,
    SupplierTransactionAccepted,
    SupplierTransactionStatus,
)
from supplymesh.edi.x12_855 import AcknowledgmentLine, generate_855
from supplymesh.edi.x12_865 import ChangeResponseLine, generate_865
from supplymesh.persistence.orm_models import (
    ApprovalDecisionRecord,
    ChangeOrderRecord,
    EdiDocumentRecord,
    ExternalSideEffectRecord,
    PurchaseOrderLineRecord,
    PurchaseOrderRecord,
    SupplierTransactionRecord,
)
from supplymesh.persistence.procurement_repository import ProcurementRepository
from supplymesh.persistence.side_effect_journal import SqlAlchemySideEffectJournal
from supplymesh.protocols.procurement import (
    ChangeOrderSubmitted,
    ChangeResponseReceived,
    CounterproposalExecutionRequest,
    PurchaseOrderExecutionRequest,
)
from supplymesh.services.approval_service import approval_payload_hash


class SupplierGateway:
    def __init__(self, po: PurchaseOrder) -> None:
        self.po = po
        self.po_submissions = 0
        self.change_submissions = 0
        self.po_status_checks = 0

    async def submit_purchase_order(
        self,
        supplier_id: str,
        submission: PurchaseOrderSubmission,
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> SupplierTransactionAccepted:
        del supplier_id, submission, idempotency_key, correlation_id
        self.po_submissions += 1
        return SupplierTransactionAccepted(
            supplier_transaction_id="supplier-po-1",
            poll_url="/purchase-orders/supplier-po-1",
            estimated_ready_at=datetime(2026, 10, 16, tzinfo=UTC),
        )

    async def get_purchase_order_status(
        self, supplier_id: str, transaction_id: str, *, correlation_id: str
    ) -> SupplierTransactionStatus:
        del supplier_id, transaction_id, correlation_id
        self.po_status_checks += 1
        return SupplierTransactionStatus(
            status="ACKNOWLEDGED",
            document_type="855",
            x12=generate_855(
                po_number=self.po.po_number,
                po_version=self.po.version,
                currency=self.po.currency,
                lines=(
                    AcknowledgmentLine(
                        line_number=1,
                        component_sku="ENGINE-001",
                        status_code="IA",
                        accepted_quantity=Decimal("75"),
                        unit="EA",
                        unit_price=Decimal("10"),
                        promised_date=date(2026, 12, 1),
                    ),
                ),
                control_number="8551",
            ),
        )

    async def submit_change_order(
        self,
        supplier_id: str,
        submission: ChangeOrderSubmission,
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> SupplierTransactionAccepted:
        del supplier_id, submission, idempotency_key, correlation_id
        self.change_submissions += 1
        return SupplierTransactionAccepted(
            supplier_transaction_id="supplier-change-1",
            poll_url="/change-orders/supplier-change-1",
            estimated_ready_at=datetime(2026, 10, 16, tzinfo=UTC),
        )

    async def get_change_order_status(
        self, supplier_id: str, transaction_id: str, *, correlation_id: str
    ) -> ChangeTransactionStatus:
        del supplier_id, transaction_id, correlation_id
        return ChangeTransactionStatus(
            status="ACKNOWLEDGED",
            document_type="865",
            x12=generate_865(
                po_number=self.po.po_number,
                po_version=2,
                currency=self.po.currency,
                lines=(
                    ChangeResponseLine(
                        line_number=1,
                        component_sku="ENGINE-001",
                        response_code="IA",
                        quantity=Decimal("75"),
                        unit="EA",
                        unit_price=Decimal("10"),
                        promised_date=date(2026, 12, 1),
                    ),
                ),
                control_number="8651",
            ),
        )


class Context:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []
        self.logger = logging.getLogger("procurement-recovery-test")

    async def send(self, destination: str, message: object, timeout: int = 30) -> None:
        del timeout
        self.sent.append((destination, message))


class CrashAfterSupplierAcceptance:
    def __init__(self, repository: ProcurementRepository, method: str) -> None:
        self.repository = repository
        self.method = method

    def __getattr__(self, name: str) -> Any:
        return getattr(self.repository, name)

    def record_submission(self, *args: object, **kwargs: object) -> None:
        if self.method == "purchase_order":
            raise RuntimeError("simulated process crash after supplier acceptance")
        self.repository.record_submission(*args, **kwargs)  # type: ignore[arg-type]

    def record_change_submission(self, *args: object, **kwargs: object) -> None:
        if self.method == "change_order":
            raise RuntimeError("simulated process crash after supplier acceptance")
        self.repository.record_change_submission(*args, **kwargs)  # type: ignore[arg-type]


def purchase_order() -> PurchaseOrder:
    return PurchaseOrder(
        po_id=uuid4(),
        po_number="PO-RECOVERY-1",
        workflow_id=uuid4(),
        supplier_id="supplier-alpha",
        version=1,
        currency="USD",
        lines=(
            PurchaseOrderLine(
                line_number=1,
                component_sku="ENGINE-001",
                quantity=Decimal("75"),
                unit_price=Decimal("10"),
                requested_date=date(2026, 12, 1),
            ),
        ),
        total=Decimal("750"),
    )


def approval(target: PurchaseOrder | Counterproposal, decision_type: str) -> ApprovalRecord:
    target_version = (
        target.version if isinstance(target, PurchaseOrder) else target.current_po_version
    )
    return ApprovalRecord(
        approval_id=uuid4(),
        workflow_id=target.workflow_id,
        decision_type=decision_type,
        target_type="purchase_order",
        target_id=str(target.po_id),
        target_version=target_version,
        decision="APPROVED",
        actor="manager",
        payload_hash=approval_payload_hash(target),
        decided_at=datetime(2026, 10, 16, tzinfo=UTC),
    )


def session_factory(path: Path) -> tuple[Any, Callable[[], Session]]:
    engine = create_engine(f"sqlite+pysqlite:///{path}")

    def sessions() -> Session:
        return Session(engine)

    return engine, sessions


def create_tables(engine: Any) -> None:
    for table in (
        ApprovalDecisionRecord.__table__,
        PurchaseOrderRecord.__table__,
        PurchaseOrderLineRecord.__table__,
        EdiDocumentRecord.__table__,
        ChangeOrderRecord.__table__,
        SupplierTransactionRecord.__table__,
        ExternalSideEffectRecord.__table__,
    ):
        table.create(engine)


def persist_approval(sessions: Callable[[], Session], approved: ApprovalRecord) -> None:
    with sessions() as session, session.begin():
        session.add(
            ApprovalDecisionRecord(
                id=approved.approval_id,
                workflow_id=approved.workflow_id,
                decision_type=approved.decision_type,
                target_type=approved.target_type,
                target_id=approved.target_id,
                target_version=approved.target_version,
                decision=approved.decision,
                actor=approved.actor,
                payload_hash=approved.payload_hash,
                decided_at=approved.decided_at,
            )
        )


@pytest.mark.asyncio
async def test_purchase_order_recovers_after_restart_without_duplicate_submission(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "po-recovery.db"
    engine, sessions = session_factory(database_path)
    create_tables(engine)
    po = purchase_order()
    approved = approval(po, "SEND_PURCHASE_ORDER")
    persist_approval(sessions, approved)
    gateway = SupplierGateway(po)
    repository = ProcurementRepository(sessions)
    service = ProcurementAgentService(
        gateway,
        SqlAlchemySideEffectJournal(sessions),
        CrashAfterSupplierAcceptance(repository, "purchase_order"),  # type: ignore[arg-type]
    )
    request = PurchaseOrderExecutionRequest(
        correlation_id=uuid4(),
        workflow_id=po.workflow_id,
        sender_agent="manufacturing-orchestrator",
        purchase_orders=(po,),
        approval_id=str(approved.approval_id),
    )

    with pytest.raises(RuntimeError, match="simulated process crash"):
        await service.execute_purchase_orders(request, reply_address="agent1orchestrator")
    engine.dispose()

    restarted_engine, restarted_sessions = session_factory(database_path)
    restarted = ProcurementAgentService(
        gateway,
        SqlAlchemySideEffectJournal(restarted_sessions),
        ProcurementRepository(restarted_sessions),
    )
    protocol = build_procurement_protocol(restarted, recovery_interval_seconds=0.01)
    interval_handler, interval_seconds = protocol.intervals[0]
    context = Context()
    await interval_handler(context)

    assert gateway.po_submissions == 1
    assert interval_seconds == 0.01
    assert [destination for destination, _ in context.sent] == [
        "agent1orchestrator",
        "agent1orchestrator",
    ]
    assert isinstance(context.sent[0][1], PurchaseOrderSubmittedTransport)
    assert isinstance(context.sent[1][1], AcknowledgmentReceivedTransport)
    submitted = context.sent[0][1].to_domain()
    acknowledgment = context.sent[1][1].to_domain()
    assert acknowledgment.reply_to == str(submitted.message_id)
    with restarted_sessions() as session:
        transaction = session.scalar(select(SupplierTransactionRecord))
        documents = tuple(session.scalars(select(EdiDocumentRecord)))
    assert transaction is not None
    assert transaction.status == "DELIVERED"
    assert {document.document_type for document in documents} == {"850", "855"}
    restarted_engine.dispose()


@pytest.mark.asyncio
async def test_change_order_recovers_after_restart_without_duplicate_submission(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "change-recovery.db"
    engine, sessions = session_factory(database_path)
    create_tables(engine)
    po = purchase_order()
    repository = ProcurementRepository(sessions)
    repository.save_approved_purchase_order(po)
    counter = Counterproposal(
        workflow_id=po.workflow_id,
        po_id=po.po_id,
        po_number=po.po_number,
        current_po_version=po.version,
        change_version=2,
        supplier_id=po.supplier_id,
        currency=po.currency,
        lines=(
            CounterproposalLine(
                line_number=1,
                component_sku="ENGINE-001",
                quantity=Decimal("75"),
                unit="EA",
                maximum_unit_price=Decimal("10"),
                requested_date=date(2026, 12, 1),
            ),
        ),
    )
    approved = approval(counter, "SEND_COUNTERPROPOSAL")
    persist_approval(sessions, approved)
    gateway = SupplierGateway(po)
    service = ProcurementAgentService(
        gateway,
        SqlAlchemySideEffectJournal(sessions),
        CrashAfterSupplierAcceptance(repository, "change_order"),  # type: ignore[arg-type]
    )
    request = CounterproposalExecutionRequest(
        correlation_id=uuid4(),
        workflow_id=po.workflow_id,
        sender_agent="manufacturing-orchestrator",
        counterproposal=counter,
        approval_id=str(approved.approval_id),
    )

    with pytest.raises(RuntimeError, match="simulated process crash"):
        await service.execute_counterproposal(request, reply_address="agent1orchestrator")
    engine.dispose()

    restarted_engine, restarted_sessions = session_factory(database_path)
    restarted = ProcurementAgentService(
        gateway,
        SqlAlchemySideEffectJournal(restarted_sessions),
        ProcurementRepository(restarted_sessions),
    )
    deliveries = await restarted.recover_pending()
    restarted.mark_delivery_complete(deliveries[0].submitted)

    assert gateway.change_submissions == 1
    assert len(deliveries) == 1
    assert isinstance(deliveries[0].submitted, ChangeOrderSubmitted)
    assert isinstance(deliveries[0].response, ChangeResponseReceived)
    assert deliveries[0].response.reply_to == str(deliveries[0].submitted.message_id)
    with restarted_sessions() as session:
        transaction = session.scalar(select(SupplierTransactionRecord))
        documents = tuple(session.scalars(select(EdiDocumentRecord)))
    assert transaction is not None
    assert transaction.status == "DELIVERED"
    assert {document.document_type for document in documents} == {"860", "865"}
    restarted_engine.dispose()


@pytest.mark.asyncio
async def test_ready_delivery_is_replayed_after_restart_and_leased_once(tmp_path: Path) -> None:
    database_path = tmp_path / "ready-delivery-recovery.db"
    engine, sessions = session_factory(database_path)
    create_tables(engine)
    po = purchase_order()
    approved = approval(po, "SEND_PURCHASE_ORDER")
    persist_approval(sessions, approved)
    gateway = SupplierGateway(po)
    repository = ProcurementRepository(sessions)
    service = ProcurementAgentService(
        gateway,
        SqlAlchemySideEffectJournal(sessions),
        repository,
    )
    request = PurchaseOrderExecutionRequest(
        correlation_id=uuid4(),
        workflow_id=po.workflow_id,
        sender_agent="manufacturing-orchestrator",
        purchase_orders=(po,),
        approval_id=str(approved.approval_id),
    )

    result = await service.execute_purchase_orders(request, reply_address="agent1orchestrator")
    assert len(result) == 1
    with sessions() as session:
        transaction = session.scalar(select(SupplierTransactionRecord))
        assert transaction is not None
        assert transaction.status == "READY_TO_DELIVER"
    engine.dispose()

    restarted_engine, restarted_sessions = session_factory(database_path)
    first_worker = ProcurementAgentService(
        gateway,
        SqlAlchemySideEffectJournal(restarted_sessions),
        ProcurementRepository(restarted_sessions),
    )
    second_worker = ProcurementAgentService(
        gateway,
        SqlAlchemySideEffectJournal(restarted_sessions),
        ProcurementRepository(restarted_sessions),
    )
    first_delivery = await first_worker.recover_pending()
    second_delivery = await second_worker.recover_pending()

    assert len(first_delivery) == 1
    assert second_delivery == ()
    assert gateway.po_submissions == 1
    assert gateway.po_status_checks == 1
    first_worker.mark_delivery_complete(first_delivery[0].submitted)
    with restarted_sessions() as session:
        delivered = session.scalar(select(SupplierTransactionRecord))
        assert delivered is not None
        assert delivered.status == "DELIVERED"
        assert delivered.lease_owner is None
        assert delivered.lease_expires_at is None
    restarted_engine.dispose()
