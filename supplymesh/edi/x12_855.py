from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from supplymesh.edi.common import envelope, validate_envelope

KNOWN = {"BAK", "REF", "CUR", "PO1", "ACK", "DTM"}


class AcknowledgmentLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    line_number: int
    component_sku: str
    status_code: str
    accepted_quantity: Decimal
    unit: str
    unit_price: Decimal
    promised_date: date


class Parsed855(BaseModel):
    model_config = ConfigDict(frozen=True)

    po_number: str
    po_version: int
    currency: str
    lines: tuple[AcknowledgmentLine, ...]
    raw_text: str
    unknown_segments: tuple[str, ...]


def generate_855(
    *,
    po_number: str,
    po_version: int,
    currency: str,
    lines: tuple[AcknowledgmentLine, ...],
    control_number: str,
) -> str:
    body = [
        f"BAK*00*AC*{po_number}*{date.today().strftime('%Y%m%d')}",
        f"REF*PV*{po_version}",
        f"CUR*BY*{currency}",
    ]
    for line in lines:
        body.extend(
            [
                f"PO1*{line.line_number}*{line.accepted_quantity}*{line.unit}*{line.unit_price}**BP*{line.component_sku}",
                f"ACK*{line.status_code}*{line.accepted_quantity}*{line.unit}*{line.promised_date.strftime('%Y%m%d')}",
                f"DTM*068*{line.promised_date.strftime('%Y%m%d')}",
            ]
        )
    return envelope("855", control_number, body)


def parse_855(raw_text: str) -> Parsed855:
    document = validate_envelope(raw_text, "855", KNOWN)
    bak = next((segment for segment in document.segments if segment[0] == "BAK"), None)
    version = next((segment for segment in document.segments if segment[:2] == ("REF", "PV")), None)
    currency = next((segment for segment in document.segments if segment[0] == "CUR"), None)
    if bak is None or version is None or currency is None:
        raise ValueError("855 is missing BAK, PO version, or currency")
    lines: list[AcknowledgmentLine] = []
    segments = list(document.segments)
    for index, segment in enumerate(segments):
        if segment[0] != "PO1":
            continue
        if index + 1 >= len(segments) or segments[index + 1][0] != "ACK":
            raise ValueError("855 PO1 is missing following ACK")
        ack = segments[index + 1]
        lines.append(
            AcknowledgmentLine(
                line_number=int(segment[1]),
                component_sku=segment[7],
                status_code=ack[1],
                accepted_quantity=Decimal(ack[2]),
                unit=ack[3],
                unit_price=Decimal(segment[4]),
                promised_date=date.fromisoformat(f"{ack[4][:4]}-{ack[4][4:6]}-{ack[4][6:]}"),
            )
        )
    return Parsed855(
        po_number=bak[3],
        po_version=int(version[2]),
        currency=currency[2],
        lines=tuple(lines),
        raw_text=raw_text,
        unknown_segments=document.unknown_segments,
    )
