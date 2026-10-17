from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from supplymesh.domain.procurement import (
    ApprovalRecord,
    Counterproposal,
    CounterproposalLine,
    PurchaseOrder,
    PurchaseOrderLine,
)
from supplymesh.edi.x12_850 import generate_850
from supplymesh.edi.x12_855 import AcknowledgmentLine, generate_855, parse_855
from supplymesh.edi.x12_860 import ChangeRequestLine, generate_860
from supplymesh.edi.x12_865 import ChangeResponseLine, generate_865
from supplymesh.persistence.orm_models import (
    AcknowledgmentLineRecord,
    AcknowledgmentRecord,
    ApprovalDecisionRecord,
    ChangeOrderRecord,
    EdiDocumentRecord,
    ExceptionRecord,
    PurchaseOrderLineRecord,
    PurchaseOrderRecord,
    SupplierTransactionRecord,
)
from supplymesh.persistence.procurement_repository import ProcurementRepository
from supplymesh.services.approval_service import approval_payload_hash
from supplymesh.services.comparison_service import compare_acknowledgment


def test_procurement_repository_persists_approval_po_and_supplier_transaction() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, connection_record: object) -> None:
        del connection_record
        dbapi_connection.execute("PRAGMA foreign_keys=ON")  # type: ignore[attr-defined]

    for table in (
        ApprovalDecisionRecord.__table__,
        PurchaseOrderRecord.__table__,
        PurchaseOrderLineRecord.__table__,
        SupplierTransactionRecord.__table__,
    ):
        table.create(engine)

    def sessions() -> Session:
        return Session(engine)

    po = PurchaseOrder(
        po_id=uuid4(),
        po_number="PO-REPO-1",
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
    approval = ApprovalRecord(
        approval_id=uuid4(),
        workflow_id=po.workflow_id,
        decision_type="SEND_PURCHASE_ORDER",
        target_type="purchase_order",
        target_id=str(po.po_id),
        target_version=po.version,
        decision="APPROVED",
        actor="manager",
        payload_hash=approval_payload_hash(po),
        decided_at=datetime(2026, 10, 16, tzinfo=UTC),
    )
    with sessions() as session, session.begin():
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
                payload_hash=approval.payload_hash,
                decided_at=approval.decided_at,
            )
        )

    repository = ProcurementRepository(sessions)
    loaded_approval = repository.get_approval(str(approval.approval_id))
    assert loaded_approval.approval_id == approval.approval_id
    assert loaded_approval.payload_hash == approval.payload_hash
    assert loaded_approval.target_id == approval.target_id
    repository.save_approved_purchase_order(po)
    repository.save_approved_purchase_order(po)
    assert repository.get_purchase_order(str(po.po_id)) == po
    repository.record_submission(
        po, external_transaction_id="TX-REPO-1", poll_url="/purchase-orders/TX-REPO-1"
    )
    repository.record_submission(
        po, external_transaction_id="TX-REPO-1", poll_url="/purchase-orders/TX-REPO-1"
    )

    with sessions() as session:
        row = session.get(PurchaseOrderRecord, po.po_id)
        transactions = tuple(session.scalars(select(SupplierTransactionRecord)))
        assert row is not None
        assert row.status == "SUBMITTED"
        assert len(transactions) == 1

    altered = po.model_copy(update={"total": Decimal("999")})
    with pytest.raises(ValueError, match="different content"):
        repository.save_approved_purchase_order(altered)
    engine.dispose()


def test_procurement_evidence_is_immutable_idempotent_and_structured() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, connection_record: object) -> None:
        del connection_record
        dbapi_connection.execute("PRAGMA foreign_keys=ON")  # type: ignore[attr-defined]

    for table in (
        PurchaseOrderRecord.__table__,
        PurchaseOrderLineRecord.__table__,
        EdiDocumentRecord.__table__,
        AcknowledgmentRecord.__table__,
        AcknowledgmentLineRecord.__table__,
        ExceptionRecord.__table__,
        ChangeOrderRecord.__table__,
    ):
        table.create(engine)

    def sessions() -> Session:
        return Session(engine)

    po = PurchaseOrder(
        po_id=uuid4(),
        po_number="PO-EVIDENCE-1",
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
    repository = ProcurementRepository(sessions)
    repository.save_approved_purchase_order(po)
    raw_850 = generate_850(po, "101")
    first_document = repository.record_edi_document(
        workflow_id=po.workflow_id,
        supplier_id=po.supplier_id,
        document_type="850",
        direction="OUTBOUND",
        raw_text=raw_850,
        related_document_id=po.po_id,
    )
    assert first_document == repository.record_edi_document(
        workflow_id=po.workflow_id,
        supplier_id=po.supplier_id,
        document_type="850",
        direction="OUTBOUND",
        raw_text=raw_850,
        related_document_id=po.po_id,
    )

    raw_855 = generate_855(
        po_number=po.po_number,
        po_version=po.version,
        currency=po.currency,
        lines=(
            AcknowledgmentLine(
                line_number=1,
                component_sku="ENGINE-001",
                status_code="IA",
                accepted_quantity=Decimal("75"),
                unit="EA",
                unit_price=Decimal("11"),
                promised_date=date(2026, 12, 1),
            ),
        ),
        control_number="102",
    )
    parsed = parse_855(raw_855)
    comparison = compare_acknowledgment(po, parsed)
    repository.record_edi_document(
        workflow_id=po.workflow_id,
        supplier_id=po.supplier_id,
        document_type="855",
        direction="INBOUND",
        raw_text=raw_855,
        related_document_id=po.po_id,
    )
    acknowledgment_id = repository.record_acknowledgment_comparison(
        po=po, parsed=parsed, comparison=comparison
    )
    assert acknowledgment_id == repository.record_acknowledgment_comparison(
        po=po, parsed=parsed, comparison=comparison
    )

    counter = Counterproposal(
        workflow_id=po.workflow_id,
        po_id=po.po_id,
        po_number=po.po_number,
        current_po_version=1,
        change_version=2,
        supplier_id=po.supplier_id,
        currency=po.currency,
        lines=(
            CounterproposalLine(
                line_number=1,
                component_sku="ENGINE-001",
                quantity=Decimal("75"),
                unit="EA",
                maximum_unit_price=Decimal("10.50"),
                requested_date=date(2026, 12, 1),
            ),
        ),
    )
    repository.record_change_order(counter, approval_id=uuid4())
    raw_860 = generate_860(
        po_number=counter.po_number,
        po_version=counter.change_version,
        currency=counter.currency,
        lines=tuple(
            ChangeRequestLine(
                line_number=line.line_number,
                component_sku=line.component_sku,
                quantity=line.quantity,
                unit=line.unit,
                maximum_unit_price=line.maximum_unit_price,
                requested_date=line.requested_date,
            )
            for line in counter.lines
        ),
        control_number="103",
    )
    repository.record_edi_document(
        workflow_id=po.workflow_id,
        supplier_id=po.supplier_id,
        document_type="860",
        direction="OUTBOUND",
        raw_text=raw_860,
        related_document_id=po.po_id,
    )
    raw_865 = generate_865(
        po_number=po.po_number,
        po_version=2,
        currency=po.currency,
        lines=(
            ChangeResponseLine(
                line_number=1,
                component_sku="ENGINE-001",
                response_code="IA",
                quantity=Decimal("75"),
                unit="EA",
                unit_price=Decimal("10.50"),
                promised_date=date(2026, 12, 1),
            ),
        ),
        control_number="104",
    )
    repository.record_edi_document(
        workflow_id=po.workflow_id,
        supplier_id=po.supplier_id,
        document_type="865",
        direction="INBOUND",
        raw_text=raw_865,
        related_document_id=po.po_id,
    )
    repository.record_change_result(po.po_id, 2, accepted=True)

    with sessions() as session:
        assert len(tuple(session.scalars(select(EdiDocumentRecord)))) == 4
        assert len(tuple(session.scalars(select(AcknowledgmentRecord)))) == 1
        assert len(tuple(session.scalars(select(AcknowledgmentLineRecord)))) == 1
        assert len(tuple(session.scalars(select(ExceptionRecord)))) == 1
        change = session.scalar(select(ChangeOrderRecord))
        assert change is not None
        assert change.status == "ACCEPTED"

    invalid_document = repository.record_edi_document(
        workflow_id=po.workflow_id,
        supplier_id=po.supplier_id,
        document_type="855",
        direction="INBOUND",
        raw_text="MALFORMED-X12",
        related_document_id=po.po_id,
    )
    with sessions() as session:
        invalid = session.get(EdiDocumentRecord, invalid_document)
        assert invalid is not None
        assert invalid.validation_status == "INVALID"
        assert invalid.raw_text == "MALFORMED-X12"
        assert "error" in invalid.parsed_json
    engine.dispose()
