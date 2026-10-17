from datetime import date
from decimal import Decimal

from supplymesh.edi.x12_860 import ChangeRequestLine, generate_860, parse_860
from supplymesh.edi.x12_865 import ChangeResponseLine, generate_865, parse_865


def test_860_and_865_round_trip_counterproposal() -> None:
    request_line = ChangeRequestLine(
        line_number=1,
        component_sku="ENGINE-001",
        quantity=Decimal("75"),
        unit="EA",
        maximum_unit_price=Decimal("825"),
        requested_date=date(2026, 12, 1),
    )
    request = parse_860(
        generate_860(
            po_number="PO-1",
            po_version=2,
            currency="USD",
            lines=(request_line,),
            control_number="77",
        )
    )
    assert request.po_version == 2
    assert request.lines[0].maximum_unit_price == Decimal("825")
    response = parse_865(
        generate_865(
            po_number=request.po_number,
            po_version=request.po_version,
            currency=request.currency,
            lines=(
                ChangeResponseLine(
                    line_number=1,
                    component_sku="ENGINE-001",
                    response_code="IA",
                    quantity=Decimal("75"),
                    unit="EA",
                    unit_price=Decimal("825"),
                    promised_date=date(2026, 12, 1),
                ),
            ),
            control_number="78",
        )
    )
    assert response.lines[0].response_code == "IA"
