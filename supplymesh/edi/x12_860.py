from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from supplymesh.edi.common import envelope, validate_envelope

KNOWN = {"BCH", "REF", "CUR", "POC", "DTM"}


class ChangeRequestLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    line_number: int
    component_sku: str
    quantity: Decimal
    unit: str
    maximum_unit_price: Decimal
    requested_date: date


class Parsed860(BaseModel):
    model_config = ConfigDict(frozen=True)

    po_number: str
    po_version: int
    currency: str
    lines: tuple[ChangeRequestLine, ...]
    raw_text: str


def generate_860(
    *,
    po_number: str,
    po_version: int,
    currency: str,
    lines: tuple[ChangeRequestLine, ...],
    control_number: str,
) -> str:
    body = [
        f"BCH*04*CP*{po_number}**{date.today().strftime('%Y%m%d')}",
        f"REF*PV*{po_version}",
        f"CUR*BY*{currency}",
    ]
    for line in lines:
        body.extend(
            [
                f"POC*{line.line_number}*CA*{line.quantity}*{line.unit}*{line.maximum_unit_price}**BP*{line.component_sku}",
                f"DTM*002*{line.requested_date.strftime('%Y%m%d')}",
            ]
        )
    return envelope("860", control_number, body)


def parse_860(raw_text: str) -> Parsed860:
    document = validate_envelope(raw_text, "860", KNOWN)
    bch = next((segment for segment in document.segments if segment[0] == "BCH"), None)
    version = next((segment for segment in document.segments if segment[:2] == ("REF", "PV")), None)
    currency = next((segment for segment in document.segments if segment[0] == "CUR"), None)
    if bch is None or version is None or currency is None:
        raise ValueError("860 is missing BCH, PO version, or currency")
    segments = list(document.segments)
    lines: list[ChangeRequestLine] = []
    for index, segment in enumerate(segments):
        if segment[0] != "POC":
            continue
        dtm = segments[index + 1]
        if dtm[:2] != ("DTM", "002"):
            raise ValueError("860 POC is missing requested-date DTM")
        raw_date = dtm[2]
        lines.append(
            ChangeRequestLine(
                line_number=int(segment[1]),
                component_sku=segment[8],
                quantity=Decimal(segment[3]),
                unit=segment[4],
                maximum_unit_price=Decimal(segment[5]),
                requested_date=date.fromisoformat(f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"),
            )
        )
    return Parsed860(
        po_number=bch[3],
        po_version=int(version[2]),
        currency=currency[2],
        lines=tuple(lines),
        raw_text=raw_text,
    )
