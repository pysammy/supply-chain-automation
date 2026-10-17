from decimal import Decimal

from supplymesh.domain.exceptions import AcknowledgmentComparison, Discrepancy, Severity
from supplymesh.domain.procurement import PurchaseOrder
from supplymesh.edi.x12_855 import Parsed855


def compare_acknowledgment(
    po: PurchaseOrder,
    acknowledgment: Parsed855,
    *,
    duplicate: bool = False,
    component_quantity_per_product: dict[str, Decimal] | None = None,
) -> AcknowledgmentComparison:
    discrepancies: list[Discrepancy] = []
    if duplicate:
        discrepancies.append(
            Discrepancy(category="DUPLICATE_ACKNOWLEDGMENT", severity=Severity.CRITICAL)
        )
    if acknowledgment.po_version != po.version:
        discrepancies.append(
            Discrepancy(
                category="STALE_PO_VERSION",
                severity=Severity.CRITICAL,
                expected_value=str(po.version),
                actual_value=str(acknowledgment.po_version),
            )
        )
    if acknowledgment.currency != po.currency:
        discrepancies.append(
            Discrepancy(
                category="CURRENCY_CHANGED",
                severity=Severity.CRITICAL,
                expected_value=po.currency,
                actual_value=acknowledgment.currency,
            )
        )

    expected_by_line = {line.line_number: line for line in po.lines}
    actual_by_line = {line.line_number: line for line in acknowledgment.lines}
    expected_cost = sum(line.quantity * line.unit_price for line in po.lines)
    actual_cost = Decimal("0")

    for line_number, actual in actual_by_line.items():
        expected = expected_by_line.get(line_number)
        if expected is None:
            discrepancies.append(
                Discrepancy(
                    category="UNKNOWN_LINE",
                    severity=Severity.CRITICAL,
                    po_line_number=line_number,
                    actual_value=actual.component_sku,
                )
            )
            continue
        actual_cost += actual.accepted_quantity * actual.unit_price
        if actual.status_code not in {"IA", "AC"}:
            discrepancies.append(
                Discrepancy(
                    category="LINE_REJECTED",
                    severity=Severity.CRITICAL,
                    po_line_number=line_number,
                    expected_value="accepted",
                    actual_value=actual.status_code,
                    unfulfilled_quantity=expected.quantity,
                )
            )
        if actual.component_sku != expected.component_sku:
            discrepancies.append(
                Discrepancy(
                    category="SUBSTITUTION_PROPOSED",
                    severity=Severity.HIGH,
                    po_line_number=line_number,
                    expected_value=expected.component_sku,
                    actual_value=actual.component_sku,
                )
            )
        if actual.unit != expected.unit:
            discrepancies.append(
                Discrepancy(
                    category="UNIT_CHANGED",
                    severity=Severity.CRITICAL,
                    po_line_number=line_number,
                    expected_value=expected.unit,
                    actual_value=actual.unit,
                )
            )
        if actual.accepted_quantity < expected.quantity:
            discrepancies.append(
                Discrepancy(
                    category="QUANTITY_REDUCED",
                    severity=Severity.HIGH,
                    po_line_number=line_number,
                    expected_value=str(expected.quantity),
                    actual_value=str(actual.accepted_quantity),
                    unfulfilled_quantity=expected.quantity - actual.accepted_quantity,
                )
            )
        elif actual.accepted_quantity > expected.quantity:
            discrepancies.append(
                Discrepancy(
                    category="QUANTITY_INCREASED",
                    severity=Severity.MEDIUM,
                    po_line_number=line_number,
                    expected_value=str(expected.quantity),
                    actual_value=str(actual.accepted_quantity),
                )
            )
        if actual.unit_price != expected.unit_price:
            price_delta = actual.unit_price - expected.unit_price
            price_percent = price_delta / expected.unit_price * 100
            severity = (
                Severity.INFORMATIONAL
                if price_delta < 0
                else Severity.HIGH
                if price_percent > 2
                else Severity.MEDIUM
            )
            discrepancies.append(
                Discrepancy(
                    category="PRICE_CHANGED",
                    severity=severity,
                    po_line_number=line_number,
                    expected_value=str(expected.unit_price),
                    actual_value=str(actual.unit_price),
                    cost_impact=price_delta * actual.accepted_quantity,
                )
            )
        schedule_delta = (actual.promised_date - expected.requested_date).days
        if schedule_delta > 0:
            discrepancies.append(
                Discrepancy(
                    category="DELIVERY_DELAYED",
                    severity=Severity.HIGH,
                    po_line_number=line_number,
                    expected_value=expected.requested_date.isoformat(),
                    actual_value=actual.promised_date.isoformat(),
                    schedule_impact_days=schedule_delta,
                )
            )
        elif schedule_delta < 0:
            discrepancies.append(
                Discrepancy(
                    category="DELIVERY_EARLIER",
                    severity=Severity.INFORMATIONAL,
                    po_line_number=line_number,
                    expected_value=expected.requested_date.isoformat(),
                    actual_value=actual.promised_date.isoformat(),
                )
            )

    for line_number, expected in expected_by_line.items():
        if line_number not in actual_by_line:
            discrepancies.append(
                Discrepancy(
                    category="MISSING_LINE",
                    severity=Severity.CRITICAL,
                    po_line_number=line_number,
                    expected_value=expected.component_sku,
                    unfulfilled_quantity=expected.quantity,
                )
            )

    cost_delta = actual_cost - expected_cost
    cost_percentage = Decimal("0") if expected_cost == 0 else cost_delta / expected_cost * 100
    unfulfilled = sum(item.unfulfilled_quantity for item in discrepancies)
    quantity_per = component_quantity_per_product or {}
    units_affected = max(
        (
            item.unfulfilled_quantity
            / quantity_per.get(expected_by_line[item.po_line_number].component_sku, Decimal("1"))
            for item in discrepancies
            if item.unfulfilled_quantity > 0 and item.po_line_number in expected_by_line
        ),
        default=Decimal("0"),
    )
    delay = max((item.schedule_impact_days for item in discrepancies), default=0)
    material = [item for item in discrepancies if item.severity != Severity.INFORMATIONAL]
    quarantined = any(
        item.category in {"DUPLICATE_ACKNOWLEDGMENT", "STALE_PO_VERSION"} for item in discrepancies
    )
    status = "QUARANTINED" if quarantined else "EXCEPTION" if material else "MATCHED"
    recommendation = _recommend(discrepancies, status)
    return AcknowledgmentComparison(
        status=status,
        discrepancies=tuple(discrepancies),
        absolute_cost_delta=cost_delta,
        percentage_cost_delta=cost_percentage,
        unfulfilled_quantity=unfulfilled,
        production_units_affected=units_affected,
        estimated_schedule_delay_days=delay,
        recommended_action=recommendation,
        allowed_actions=(
            () if status == "MATCHED" else ("accept", "counter", "switch", "split", "cancel")
        ),
    )


def _recommend(discrepancies: list[Discrepancy], status: str) -> str:
    if status == "MATCHED":
        return "complete"
    categories = {item.category for item in discrepancies}
    if categories & {"DUPLICATE_ACKNOWLEDGMENT", "STALE_PO_VERSION", "LINE_REJECTED"}:
        return "cancel"
    if "QUANTITY_REDUCED" in categories:
        return "split"
    if "DELIVERY_DELAYED" in categories:
        return "switch"
    if "PRICE_CHANGED" in categories:
        return "counter"
    return "accept"
