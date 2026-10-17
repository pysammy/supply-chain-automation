import os

from fastapi import FastAPI, Header, HTTPException, Query, Response, status

from apps.supplier_simulator.config import load_supplier_profiles
from apps.supplier_simulator.service import RfqService
from supplymesh.domain.procurement import ChangeOrderSubmission, PurchaseOrderSubmission
from supplymesh.domain.sourcing import RfqRequest


def create_app(*, api_key: str | None = None, admin_api_key: str | None = None) -> FastAPI:
    application = FastAPI(title="SupplyMesh Supplier Simulator", version="0.1.0")
    service = RfqService(load_supplier_profiles())
    expected_api_key = api_key or os.getenv("SUPPLIER_API_KEY")
    expected_admin_key = admin_api_key or os.getenv("SUPPLIER_ADMIN_API_KEY")
    app_env = os.getenv("APP_ENV", "development")

    def authenticate(value: str | None) -> None:
        if not expected_api_key:
            raise HTTPException(status_code=503, detail="supplier API key is not configured")
        if value != expected_api_key:
            raise HTTPException(status_code=401, detail="invalid API key")

    def authenticate_admin(value: str | None) -> None:
        if app_env == "production":
            raise HTTPException(status_code=404, detail="not found")
        if not expected_admin_key or value != expected_admin_key:
            raise HTTPException(status_code=401, detail="invalid admin API key")

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "supplier-simulator"}

    @application.get("/ready")
    def ready(response: Response) -> dict[str, str]:
        configured = bool(expected_api_key and (expected_admin_key or app_env == "production"))
        if not configured:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "not_ready", "reason": "required API keys are not configured"}
        return {"status": "ready", "service": "supplier-simulator"}

    @application.get("/api/v1/suppliers/{supplier_id}/capacity")
    def capacity(
        supplier_id: str,
        component_sku: str = Query(min_length=1),
        x_api_key: str | None = Header(default=None),
        x_correlation_id: str = Header(min_length=1),
    ) -> dict[str, str | None]:
        del x_correlation_id
        authenticate(x_api_key)
        try:
            return service.capacity(supplier_id, component_sku)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="supplier or component not found") from exc

    @application.post("/api/v1/suppliers/{supplier_id}/rfqs")
    def create_rfq(
        supplier_id: str,
        request: RfqRequest,
        x_api_key: str | None = Header(default=None),
        idempotency_key: str = Header(min_length=16),
        x_correlation_id: str = Header(min_length=1),
    ) -> dict[str, object]:
        del x_correlation_id
        authenticate(x_api_key)
        if supplier_id not in service.profiles:
            raise HTTPException(status_code=404, detail="supplier not found")
        try:
            service.before_rfq(supplier_id)
        except (RuntimeError, TimeoutError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return service.quote(supplier_id, request, idempotency_key).model_dump(mode="json")

    @application.post("/api/v1/suppliers/{supplier_id}/purchase-orders", status_code=202)
    def submit_purchase_order(
        supplier_id: str,
        submission: PurchaseOrderSubmission,
        x_api_key: str | None = Header(default=None),
        idempotency_key: str = Header(min_length=16),
        x_correlation_id: str = Header(min_length=1),
    ) -> dict[str, object]:
        del x_correlation_id
        authenticate(x_api_key)
        if supplier_id not in service.profiles:
            raise HTTPException(status_code=404, detail="supplier not found")
        try:
            accepted = service.submit_po(supplier_id, submission, idempotency_key)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return accepted.model_dump(mode="json")

    @application.get("/api/v1/suppliers/{supplier_id}/purchase-orders/{transaction_id}")
    def purchase_order_status(
        supplier_id: str,
        transaction_id: str,
        x_api_key: str | None = Header(default=None),
        x_correlation_id: str = Header(min_length=1),
    ) -> dict[str, object]:
        del x_correlation_id
        authenticate(x_api_key)
        try:
            return service.po_status(supplier_id, transaction_id).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="transaction not found") from exc

    @application.post("/api/v1/suppliers/{supplier_id}/change-orders", status_code=202)
    def submit_change_order(
        supplier_id: str,
        submission: ChangeOrderSubmission,
        x_api_key: str | None = Header(default=None),
        idempotency_key: str = Header(min_length=16),
        x_correlation_id: str = Header(min_length=1),
    ) -> dict[str, object]:
        del x_correlation_id
        authenticate(x_api_key)
        if supplier_id not in service.profiles:
            raise HTTPException(status_code=404, detail="supplier not found")
        try:
            accepted = service.submit_change_order(supplier_id, submission, idempotency_key)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return accepted.model_dump(mode="json")

    @application.get("/api/v1/suppliers/{supplier_id}/change-orders/{transaction_id}")
    def change_order_status(
        supplier_id: str,
        transaction_id: str,
        x_api_key: str | None = Header(default=None),
        x_correlation_id: str = Header(min_length=1),
    ) -> dict[str, object]:
        del x_correlation_id
        authenticate(x_api_key)
        try:
            return service.change_status(supplier_id, transaction_id).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="transaction not found") from exc

    @application.post("/api/v1/demo/reset")
    def reset_demo(x_api_key: str | None = Header(default=None)) -> dict[str, str]:
        authenticate_admin(x_api_key)
        service.reset()
        return {"status": "reset", "scenario": service.scenario}

    @application.post("/api/v1/demo/scenario/{scenario_name}")
    def set_demo_scenario(
        scenario_name: str, x_api_key: str | None = Header(default=None)
    ) -> dict[str, str]:
        authenticate_admin(x_api_key)
        try:
            service.set_scenario(scenario_name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"status": "configured", "scenario": service.scenario}

    return application


app = create_app()
