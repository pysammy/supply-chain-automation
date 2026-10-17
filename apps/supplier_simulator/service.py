from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import uuid4

from apps.supplier_simulator.config import SupplierProfile
from supplymesh.domain.procurement import (
    ChangeOrderSubmission,
    ChangeTransactionStatus,
    PurchaseOrderSubmission,
    SupplierTransactionAccepted,
    SupplierTransactionStatus,
)
from supplymesh.domain.sourcing import QuoteLine, RfqRequest, SupplierQuote
from supplymesh.edi.x12_850 import parse_850
from supplymesh.edi.x12_855 import AcknowledgmentLine, generate_855
from supplymesh.edi.x12_860 import parse_860
from supplymesh.edi.x12_865 import ChangeResponseLine, generate_865


class RfqService:
    def __init__(self, profiles: dict[str, SupplierProfile]) -> None:
        self._base_profiles = profiles
        self.profiles = dict(profiles)
        self.scenario = "happy_path"
        self.rfq_attempts: dict[str, int] = {}
        self._responses: dict[tuple[str, str], SupplierQuote] = {}
        self._po_responses: dict[tuple[str, str], SupplierTransactionAccepted] = {}
        self._transactions: dict[str, SupplierTransactionStatus] = {}
        self._change_responses: dict[tuple[str, str], SupplierTransactionAccepted] = {}
        self._change_transactions: dict[str, ChangeTransactionStatus] = {}

    def reset(self) -> None:
        self.profiles = {
            supplier_id: profile.model_copy(update={"ack_behavior": {"mode": "accept_exactly"}})
            for supplier_id, profile in self._base_profiles.items()
        }
        self.scenario = "happy_path"
        self.rfq_attempts.clear()
        self._responses.clear()
        self._po_responses.clear()
        self._transactions.clear()
        self._change_responses.clear()
        self._change_transactions.clear()

    def set_scenario(self, scenario: str) -> None:
        allowed = {
            "happy_path",
            "price_conflict",
            "quantity_conflict",
            "delivery_conflict",
            "multi_conflict_counter_accepted",
            "counter_rejected",
            "first_call_timeout_then_success",
            "supplier_unavailable",
            "malformed_acknowledgment",
        }
        if scenario not in allowed:
            raise ValueError(f"unknown demo scenario {scenario}")
        self.reset()
        self.scenario = scenario
        mode_by_scenario = {
            "price_conflict": ("supplier-beta", "price_conflict"),
            "quantity_conflict": ("supplier-beta", "quantity_conflict"),
            "delivery_conflict": ("supplier-gamma", "delivery_conflict"),
            "multi_conflict_counter_accepted": ("supplier-beta", "multi_conflict"),
        }
        if scenario in mode_by_scenario:
            supplier_id, mode = mode_by_scenario[scenario]
            profile = self.profiles[supplier_id]
            self.profiles[supplier_id] = profile.model_copy(update={"ack_behavior": {"mode": mode}})
        if scenario == "counter_rejected":
            profile = self.profiles["supplier-beta"]
            self.profiles["supplier-beta"] = profile.model_copy(
                update={"counter_behavior": {"mode": "reject"}}
            )

    def before_rfq(self, supplier_id: str) -> None:
        self.rfq_attempts[supplier_id] = self.rfq_attempts.get(supplier_id, 0) + 1
        if self.scenario == "supplier_unavailable":
            raise RuntimeError("supplier unavailable")
        if (
            self.scenario == "first_call_timeout_then_success"
            and self.rfq_attempts[supplier_id] == 1
        ):
            raise TimeoutError("simulated first-call timeout")

    def capacity(self, supplier_id: str, component_sku: str) -> dict[str, str | None]:
        profile = self.profiles[supplier_id]
        item = profile.catalog.get(component_sku)
        if item is None:
            raise KeyError(component_sku)
        return {
            "supplier_id": supplier_id,
            "component_sku": component_sku,
            "available_capacity": str(item.available_capacity),
            "next_replenishment": None,
            "observed_at": datetime.now(UTC).isoformat(),
        }

    def quote(
        self,
        supplier_id: str,
        request: RfqRequest,
        idempotency_key: str,
        *,
        today: date | None = None,
    ) -> SupplierQuote:
        key = (supplier_id, idempotency_key)
        if key in self._responses:
            return self._responses[key]
        profile = self.profiles[supplier_id]
        quote_lines: list[QuoteLine] = []
        for line in request.lines:
            item = profile.catalog.get(line.component_sku)
            if item is None:
                continue
            quote_lines.append(
                QuoteLine(
                    line_number=line.line_number,
                    component_sku=line.component_sku,
                    offered_quantity=min(line.quantity, item.available_capacity),
                    unit_price=item.unit_price,
                    promised_date=(today or date.today()) + timedelta(days=item.lead_time_days),
                    lead_time_days=item.lead_time_days,
                    minimum_order_quantity=item.minimum_order_quantity,
                    packaging_multiple=item.packaging_multiple,
                )
            )
        ref_digest = sha256(f"{supplier_id}:{idempotency_key}".encode()).hexdigest()[:12].upper()
        response = SupplierQuote(
            supplier_id=supplier_id,
            supplier_quote_ref=f"Q-{ref_digest}",
            currency=profile.currency,
            valid_until=request.valid_until,
            reliability_score=profile.reliability_score,
            lines=tuple(quote_lines),
            notes=(() if quote_lines else ("No requested components are in this catalog.",)),
        )
        self._responses[key] = response
        return response

    def submit_po(
        self,
        supplier_id: str,
        submission: PurchaseOrderSubmission,
        idempotency_key: str,
    ) -> SupplierTransactionAccepted:
        key = (supplier_id, idempotency_key)
        if key in self._po_responses:
            return self._po_responses[key]
        profile = self.profiles[supplier_id]
        document = parse_850(submission.x12)
        beg = next(segment for segment in document.segments if segment[0] == "BEG")
        version = next(segment for segment in document.segments if segment[:2] == ("REF", "PV"))
        currency = next(segment for segment in document.segments if segment[0] == "CUR")
        requested_date = next(
            segment for segment in document.segments if segment[:2] == ("DTM", "002")
        )[2]
        promised = date.fromisoformat(
            f"{requested_date[:4]}-{requested_date[4:6]}-{requested_date[6:]}"
        )
        lines: list[AcknowledgmentLine] = []
        for segment in document.segments:
            if segment[0] != "PO1":
                continue
            quantity = Decimal(segment[2])
            price = Decimal(segment[4])
            mode = profile.ack_behavior["mode"]
            if mode == "price_conflict":
                price = (price * Decimal("1.03")).quantize(Decimal("0.01"))
            elif mode == "quantity_conflict":
                quantity = max(Decimal("0"), quantity - Decimal("10"))
            elif mode == "delivery_conflict":
                promised += timedelta(days=7)
            elif mode == "multi_conflict":
                price = (price * Decimal("1.03")).quantize(Decimal("0.01"))
                quantity = max(Decimal("0"), quantity - Decimal("10"))
                promised += timedelta(days=7)
            lines.append(
                AcknowledgmentLine(
                    line_number=int(segment[1]),
                    component_sku=segment[7],
                    status_code="IA",
                    accepted_quantity=quantity,
                    unit=segment[3],
                    unit_price=price,
                    promised_date=promised,
                )
            )
        transaction_id = f"SUP-TXN-{uuid4().hex[:12].upper()}"
        control = str(int(sha256(transaction_id.encode()).hexdigest()[:8], 16) % 1_000_000_000)
        ack = generate_855(
            po_number=beg[3],
            po_version=int(version[2]),
            currency=currency[2],
            lines=tuple(lines),
            control_number=control,
        )
        accepted = SupplierTransactionAccepted(
            supplier_transaction_id=transaction_id,
            poll_url=f"/api/v1/suppliers/{supplier_id}/purchase-orders/{transaction_id}",
            estimated_ready_at=datetime.now(UTC),
        )
        self._po_responses[key] = accepted
        self._transactions[transaction_id] = SupplierTransactionStatus(
            status="ACKNOWLEDGED",
            document_type="855",
            x12="MALFORMED-X12" if self.scenario == "malformed_acknowledgment" else ack,
        )
        return accepted

    def po_status(self, supplier_id: str, transaction_id: str) -> SupplierTransactionStatus:
        if supplier_id not in self.profiles or transaction_id not in self._transactions:
            raise KeyError(transaction_id)
        return self._transactions[transaction_id]

    def submit_change_order(
        self,
        supplier_id: str,
        submission: ChangeOrderSubmission,
        idempotency_key: str,
    ) -> SupplierTransactionAccepted:
        key = (supplier_id, idempotency_key)
        if key in self._change_responses:
            return self._change_responses[key]
        profile = self.profiles[supplier_id]
        change = parse_860(submission.x12)
        accepted_change = not profile.counter_behavior["mode"].startswith("reject")
        code = "IA" if accepted_change else "IR"
        response_lines = tuple(
            ChangeResponseLine(
                line_number=line.line_number,
                component_sku=line.component_sku,
                response_code=code,
                quantity=line.quantity,
                unit=line.unit,
                unit_price=line.maximum_unit_price,
                promised_date=line.requested_date,
            )
            for line in change.lines
        )
        transaction_id = f"SUP-CHG-{uuid4().hex[:12].upper()}"
        control = str(int(sha256(transaction_id.encode()).hexdigest()[:8], 16) % 1_000_000_000)
        response_x12 = generate_865(
            po_number=change.po_number,
            po_version=change.po_version,
            currency=change.currency,
            lines=response_lines,
            control_number=control,
        )
        accepted = SupplierTransactionAccepted(
            supplier_transaction_id=transaction_id,
            poll_url=f"/api/v1/suppliers/{supplier_id}/change-orders/{transaction_id}",
            estimated_ready_at=datetime.now(UTC),
        )
        self._change_responses[key] = accepted
        self._change_transactions[transaction_id] = ChangeTransactionStatus(
            status="ACKNOWLEDGED", document_type="865", x12=response_x12
        )
        return accepted

    def change_status(self, supplier_id: str, transaction_id: str) -> ChangeTransactionStatus:
        if supplier_id not in self.profiles or transaction_id not in self._change_transactions:
            raise KeyError(transaction_id)
        return self._change_transactions[transaction_id]


def stable_rfq_key(workflow_id: str, supplier_id: str, request: RfqRequest) -> str:
    material = f"rfq:{workflow_id}:{supplier_id}:{request.rfq_id}:{request.model_dump_json()}"
    return sha256(material.encode()).hexdigest()
