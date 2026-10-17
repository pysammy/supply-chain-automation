from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from supplymesh.edi.common import envelope, validate_envelope

KNOWN = {"BCA", "REF", "CUR", "POC", "ACK", "DTM"}


class ChangeResponseLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    line_number: int
    component_sku: str
    response_code: Literal["IA", "IR"]
    quantity: Decimal
    unit: str
    unit_price: Decimal
    promised_date: date


class Parsed865(BaseModel):
    model_config = ConfigDict(frozen=True)

    po_number: str
    po_version: int
    currency: str
    lines: tuple[ChangeResponseLine, ...]
    raw_text: str


def generate_865(
    *,
    po_number: str,
    po_version: int,
    currency: str,
    lines: tuple[ChangeResponseLine, ...],
    control_number: str,
) -> str:
    overall = "AC" if all(line.response_code == "IA" for line in lines) else "RJ"
    body = [
        f"BCA*00*{overall}*{po_number}*{date.today().strftime('%Y%m%d')}",
        f"REF*PV*{po_version}",
        f"CUR*BY*{currency}",
    ]
    for line in lines:
        body.extend(
            [
                f"POC*{line.line_number}*CA*{line.quantity}*{line.unit}*{line.unit_price}**BP*{line.component_sku}",
                f"ACK*{line.response_code}*{line.quantity}*{line.unit}*{line.promised_date.strftime('%Y%m%d')}",
                f"DTM*068*{line.promised_date.strftime('%Y%m%d')}",
            ]
        )
    return envelope("865", control_number, body)


def parse_865(raw_text: str) -> Parsed865:
    document = validate_envelope(raw_text, "865", KNOWN)
    bca = next((segment for segment in document.segments if segment[0] == "BCA"), None)
    version = next((segment for segment in document.segments if segment[:2] == ("REF", "PV")), None)
    currency = next((segment for segment in document.segments if segment[0] == "CUR"), None)
    if bca is None or version is None or currency is None:
        raise ValueError("865 is missing BCA, PO version, or currency")
    segments = list(document.segments)
    lines: list[ChangeResponseLine] = []
    for index, segment in enumerate(segments):
        if segment[0] != "POC":
            continue
        ack = segments[index + 1]
        if ack[0] != "ACK":
            raise ValueError("865 POC is missing following ACK")
        raw_date = ack[4]
        lines.append(
            ChangeResponseLine(
                line_number=int(segment[1]),
                component_sku=segment[8],
                response_code=ack[1],
                quantity=Decimal(ack[2]),
                unit=ack[3],
                unit_price=Decimal(segment[5]),
                promised_date=date.fromisoformat(f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"),
            )
        )
    return Parsed865(
        po_number=bca[3],
        po_version=int(version[2]),
        currency=currency[2],
        lines=tuple(lines),
        raw_text=raw_text,
    )
