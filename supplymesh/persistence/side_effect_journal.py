from collections.abc import Callable

from sqlalchemy.orm import Session

from supplymesh.persistence.orm_models import ExternalSideEffectRecord


class SqlAlchemySideEffectJournal:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    def find_success(self, idempotency_key: str) -> dict[str, str] | None:
        with self.session_factory() as session:
            record = session.get(ExternalSideEffectRecord, idempotency_key)
            if record is None or record.status != "SUCCEEDED" or record.result_json is None:
                return None
            return {str(key): str(value) for key, value in record.result_json.items()}

    def record_pending(self, idempotency_key: str, operation: str, payload_hash: str) -> None:
        with self.session_factory() as session, session.begin():
            record = session.get(ExternalSideEffectRecord, idempotency_key)
            if record is None:
                session.add(
                    ExternalSideEffectRecord(
                        idempotency_key=idempotency_key,
                        operation=operation,
                        payload_hash=payload_hash,
                        status="PENDING",
                    )
                )
            elif record.payload_hash != payload_hash or record.operation != operation:
                raise ValueError("idempotency key reused with a different operation or payload")

    def record_success(self, idempotency_key: str, result: dict[str, str]) -> None:
        with self.session_factory() as session, session.begin():
            record = session.get(ExternalSideEffectRecord, idempotency_key)
            if record is None:
                raise ValueError("cannot record success before pending side effect")
            record.result_json = result
            record.status = "SUCCEEDED"
