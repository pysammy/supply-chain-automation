import asyncio
from collections.abc import Callable

import httpx

from supplymesh.domain.procurement import (
    ChangeOrderSubmission,
    ChangeTransactionStatus,
    PurchaseOrderSubmission,
    SupplierTransactionAccepted,
    SupplierTransactionStatus,
)
from supplymesh.domain.sourcing import RfqRequest, SupplierQuote


class SupplierClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        connect_timeout: float = 3,
        read_timeout: float = 10,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], object] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = httpx.Timeout(read_timeout, connect=connect_timeout)
        self.max_retries = max_retries
        self.transport = transport
        self._sleep = sleep

    async def submit_rfq(
        self,
        supplier_id: str,
        request: RfqRequest,
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> SupplierQuote:
        headers = {
            "X-API-Key": self.api_key,
            "Idempotency-Key": idempotency_key,
            "X-Correlation-ID": correlation_id,
        }
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            transport=self.transport,
        ) as client:
            for attempt in range(self.max_retries + 1):
                try:
                    response = await client.post(
                        f"/api/v1/suppliers/{supplier_id}/rfqs",
                        headers=headers,
                        json=request.model_dump(mode="json"),
                    )
                    if response.status_code < 500:
                        response.raise_for_status()
                        return SupplierQuote.model_validate(response.json())
                except (httpx.TimeoutException, httpx.TransportError):
                    if attempt >= self.max_retries:
                        raise
                if attempt >= self.max_retries:
                    response.raise_for_status()
                delay = min(0.1 * (2**attempt), 1.0)
                if self._sleep is None:
                    await asyncio.sleep(delay)
                else:
                    result = self._sleep(delay)
                    if hasattr(result, "__await__"):
                        await result  # type: ignore[misc]
        raise RuntimeError("unreachable supplier retry state")

    async def submit_purchase_order(
        self,
        supplier_id: str,
        submission: PurchaseOrderSubmission,
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> SupplierTransactionAccepted:
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=self.timeout, transport=self.transport
        ) as client:
            response = await client.post(
                f"/api/v1/suppliers/{supplier_id}/purchase-orders",
                headers={
                    "X-API-Key": self.api_key,
                    "Idempotency-Key": idempotency_key,
                    "X-Correlation-ID": correlation_id,
                },
                json=submission.model_dump(mode="json"),
            )
            response.raise_for_status()
            return SupplierTransactionAccepted.model_validate(response.json())

    async def get_purchase_order_status(
        self, supplier_id: str, transaction_id: str, *, correlation_id: str
    ) -> SupplierTransactionStatus:
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=self.timeout, transport=self.transport
        ) as client:
            response = await client.get(
                f"/api/v1/suppliers/{supplier_id}/purchase-orders/{transaction_id}",
                headers={"X-API-Key": self.api_key, "X-Correlation-ID": correlation_id},
            )
            response.raise_for_status()
            return SupplierTransactionStatus.model_validate(response.json())

    async def submit_change_order(
        self,
        supplier_id: str,
        submission: ChangeOrderSubmission,
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> SupplierTransactionAccepted:
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=self.timeout, transport=self.transport
        ) as client:
            response = await client.post(
                f"/api/v1/suppliers/{supplier_id}/change-orders",
                headers={
                    "X-API-Key": self.api_key,
                    "Idempotency-Key": idempotency_key,
                    "X-Correlation-ID": correlation_id,
                },
                json=submission.model_dump(mode="json"),
            )
            response.raise_for_status()
            return SupplierTransactionAccepted.model_validate(response.json())

    async def get_change_order_status(
        self, supplier_id: str, transaction_id: str, *, correlation_id: str
    ) -> ChangeTransactionStatus:
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=self.timeout, transport=self.transport
        ) as client:
            response = await client.get(
                f"/api/v1/suppliers/{supplier_id}/change-orders/{transaction_id}",
                headers={"X-API-Key": self.api_key, "X-Correlation-ID": correlation_id},
            )
            response.raise_for_status()
            return ChangeTransactionStatus.model_validate(response.json())
