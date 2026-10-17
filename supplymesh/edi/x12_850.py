from supplymesh.domain.procurement import PurchaseOrder
from supplymesh.edi.common import X12Document, envelope, validate_envelope

KNOWN = {"BEG", "REF", "DTM", "N1", "CUR", "PO1", "CTT"}


def generate_850(po: PurchaseOrder, control_number: str) -> str:
    body = [
        f"BEG*00*NE*{po.po_number}**{po.lines[0].requested_date.strftime('%Y%m%d')}",
        f"REF*PV*{po.version}",
        f"REF*WF*{po.workflow_id}",
        f"CUR*BY*{po.currency}",
        f"DTM*002*{po.lines[0].requested_date.strftime('%Y%m%d')}",
        f"N1*SU*{po.supplier_id}",
    ]
    body.extend(
        f"PO1*{line.line_number}*{line.quantity}*{line.unit}*{line.unit_price}**BP*{line.component_sku}"
        for line in po.lines
    )
    body.append(f"CTT*{len(po.lines)}")
    return envelope("850", control_number, body)


def parse_850(raw_text: str) -> X12Document:
    document = validate_envelope(raw_text, "850", KNOWN)
    if not any(segment[0] == "BEG" for segment in document.segments):
        raise ValueError("850 is missing BEG")
    if not any(segment[0] == "PO1" for segment in document.segments):
        raise ValueError("850 is missing PO1")
    return document
