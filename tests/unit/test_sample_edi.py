from pathlib import Path

from supplymesh.edi.x12_850 import parse_850
from supplymesh.edi.x12_855 import parse_855
from supplymesh.edi.x12_860 import parse_860
from supplymesh.edi.x12_865 import parse_865

SAMPLES = Path(__file__).resolve().parents[2] / "docs" / "samples"


def test_documented_x12_samples_parse_with_runtime_parsers() -> None:
    purchase_order = parse_850((SAMPLES / "purchase-order-850.x12").read_text().strip())
    acknowledgment = parse_855((SAMPLES / "acknowledgment-855.x12").read_text().strip())
    change_order = parse_860((SAMPLES / "change-order-860.x12").read_text().strip())
    change_response = parse_865((SAMPLES / "change-response-865.x12").read_text().strip())

    assert purchase_order.transaction_type == "850"
    assert acknowledgment.po_number == "PO-DEMO-0001"
    assert acknowledgment.po_version == 1
    assert change_order.po_version == 2
    assert change_response.po_version == 2
    assert change_response.lines[0].response_code == "IA"
