from dataclasses import dataclass


@dataclass(frozen=True)
class X12Document:
    transaction_type: str
    control_number: str
    segments: tuple[tuple[str, ...], ...]
    unknown_segments: tuple[str, ...]
    raw_text: str


KNOWN_ENVELOPE = {"ISA", "GS", "ST", "SE", "GE", "IEA"}


def parse_segments(raw_text: str) -> tuple[tuple[str, ...], ...]:
    segments = tuple(
        tuple(element.strip() for element in segment.strip().split("*"))
        for segment in raw_text.split("~")
        if segment.strip()
    )
    if not segments or segments[0][0] != "ISA" or segments[-1][0] != "IEA":
        raise ValueError("invalid X12 envelope")
    return segments


def validate_envelope(
    raw_text: str, expected_transaction_type: str, known_segments: set[str]
) -> X12Document:
    segments = parse_segments(raw_text)
    st = next((segment for segment in segments if segment[0] == "ST"), None)
    se = next((segment for segment in segments if segment[0] == "SE"), None)
    if st is None or se is None or len(st) < 3 or len(se) < 3:
        raise ValueError("missing or malformed ST/SE segments")
    if st[1] != expected_transaction_type:
        raise ValueError(f"expected transaction {expected_transaction_type}, got {st[1]}")
    if st[2] != se[2]:
        raise ValueError("ST/SE control numbers do not match")
    start = segments.index(st)
    end = segments.index(se)
    if int(se[1]) != end - start + 1:
        raise ValueError("SE segment count does not match transaction")
    unknown = tuple(
        "*".join(segment)
        for segment in segments
        if segment[0] not in known_segments | KNOWN_ENVELOPE
    )
    return X12Document(
        transaction_type=st[1],
        control_number=st[2],
        segments=segments,
        unknown_segments=unknown,
        raw_text=raw_text,
    )


def envelope(transaction_type: str, control_number: str, body: list[str]) -> str:
    control = control_number[-9:].zfill(9)
    transaction_control = control_number[-9:].zfill(9)
    segments = [
        "ISA*00*          *00*          *ZZ*SUPPLYMESH     *ZZ*SUPPLIER       "
        f"*261016*1200*U*00401*{control}*0*P*>",
        f"GS*PO*SUPPLYMESH*SUPPLIER*20261016*1200*{control_number}*X*004010",
        f"ST*{transaction_type}*{transaction_control}",
        *body,
    ]
    segments.append(f"SE*{len(segments) - 1}*{transaction_control}")
    segments.extend([f"GE*1*{control_number}", f"IEA*1*{control}"])
    return "~".join(segments) + "~"
