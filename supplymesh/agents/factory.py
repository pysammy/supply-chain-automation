from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy.orm import Session
from uagents import Agent, Context

from supplymesh.agents.acknowledgment import (
    AcknowledgmentAgentService,
    build_acknowledgment_protocol,
)
from supplymesh.agents.inventory import build_inventory_protocol
from supplymesh.agents.orchestration import (
    OrchestrationCoordinator,
    build_specialist_response_protocol,
)
from supplymesh.agents.orchestrator import build_chat_protocol, build_planning_response_protocol
from supplymesh.agents.payment import (
    StripeCheckout,
    build_payment_seller_protocol,
    build_payment_tool_protocol,
)
from supplymesh.agents.procurement import ProcurementAgentService, build_procurement_protocol
from supplymesh.agents.sourcing import LiveFxResolver, SourcingAgentService, build_sourcing_protocol
from supplymesh.agents.supplier import (
    SupplierAgentClient,
    SupplierAgentService,
    build_supplier_chat_protocol,
    build_supplier_protocol,
)
from supplymesh.clients.supplier_client import SupplierClient
from supplymesh.persistence.audit_repository import AuditRepository
from supplymesh.persistence.database import get_engine
from supplymesh.persistence.fx_repository import SqlAlchemyFxRepository
from supplymesh.persistence.payment_repository import PaymentRepository
from supplymesh.persistence.planning_repository import PlanningRequestRepository
from supplymesh.persistence.procurement_repository import ProcurementRepository
from supplymesh.persistence.side_effect_journal import SqlAlchemySideEffectJournal
from supplymesh.persistence.workflow_repository import WorkflowRepository
from supplymesh.services.fx_service import FrankfurterFxProvider
from supplymesh.workflows.procurement_graph import build_event_transition_graph


@dataclass(frozen=True)
class AgentSeeds:
    orchestrator: str
    inventory: str
    sourcing: str
    procurement: str
    acknowledgment: str
    supplier: str
    payment: str = ""  # optional seventh agent; empty disables Stripe payments

    def validate(self) -> None:
        required = tuple(value for key, value in vars(self).items() if key != "payment")
        if any(not value.strip() for value in required):
            raise ValueError("all six agent seeds must be configured")
        values = (*required, self.payment) if self.payment.strip() else required
        if len(set(values)) != len(values):
            raise ValueError("each agent must have a unique seed")


def _specialist(
    name: str, seed: str, description: str, publish: bool, *, mailbox: bool = False
) -> Agent:
    return Agent(
        name=name,
        seed=seed,
        mailbox=mailbox,
        publish_agent_details=publish,
        description=description,
    )


def _session_factory() -> Session:
    return Session(get_engine())


def build_company_agents(
    seeds: AgentSeeds,
    *,
    publish_agent_details: bool = False,
    mailbox: bool = False,
    publish_manifests: bool = True,
    supplier_base_url: str = "http://localhost:8001",
    supplier_api_key: str = "",
    supplier_ids: tuple[str, ...] = ("supplier-alpha", "supplier-beta", "supplier-gamma"),
    http_connect_timeout_seconds: float = 3,
    http_read_timeout_seconds: float = 10,
    http_max_retries: int = 3,
    minimum_valid_quotes: int = 2,
    fx_provider_url: str = "https://api.frankfurter.dev",
    fx_cache_ttl_seconds: int = 3600,
    ack_poll_interval_seconds: float = 2,
    ack_poll_timeout_seconds: float = 45,
    stripe_secret_key: str = "",
    stripe_publishable_key: str = "",
    stripe_return_url: str = "https://asi1.ai",
) -> tuple[Agent, ...]:
    """Build five company agents, one public Supplier Agent and an optional internal Payment
    Agent in the same Bureau."""
    seeds.validate()
    if seeds.payment.strip() and not (stripe_secret_key and stripe_publishable_key):
        raise ValueError("PAYMENT_AGENT_SEED requires STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY")
    descriptions = {
        "orchestrator": (
            "orchestrator",
            (
                "Manufacturing procurement: BOM, shortages, sourcing, human-"
                "approved purchase orders and X12 acknowledgments. Start here for "
                "procurement workflows."
            ),
        ),
        "inventory": (
            "inventory-bom",
            (
                "Internal specialist: BOM expansion, protected reservations and "
                "shortages. Called only by the manufacturing Orchestrator."
            ),
        ),
        "sourcing": (
            "sourcing-optimization",
            (
                "Internal specialist: supplier quotations and constrained OR-Tools"
                " procurement plans. Called only by the manufacturing "
                "Orchestrator."
            ),
        ),
        "procurement": (
            "procurement-execution",
            (
                "Internal specialist: executes approved, version-matched, "
                "idempotent purchase and change orders. Called only by the "
                "manufacturing Orchestrator."
            ),
        ),
        "acknowledgment": (
            "acknowledgment",
            (
                "Internal specialist: validates supplier acknowledgments and "
                "exceptions. Called only by the manufacturing Orchestrator."
            ),
        ),
        "supplier": (
            "supplier",
            (
                "Supplier quotations and capacity for Alpha, Beta and Gamma. "
                "Routes approved company-agent PO/change requests to supplier REST"
                " tools. Public chat supports inquiries only."
            ),
        ),
    }
    agents = {
        role: _specialist(
            f"supplymesh-{name}",
            getattr(seeds, role),
            description,
            publish_agent_details,
            mailbox=mailbox,
        )
        for role, (name, description) in descriptions.items()
    }
    orchestrator, inventory, sourcing, procurement, acknowledgment, supplier = agents.values()
    supplier_service = SupplierAgentService(
        SupplierClient(
            base_url=supplier_base_url,
            api_key=supplier_api_key,
            connect_timeout=http_connect_timeout_seconds,
            read_timeout=http_read_timeout_seconds,
            max_retries=http_max_retries,
        ),
        supplier_ids,
    )
    supplier.include(
        build_supplier_protocol(
            supplier_service,
            sourcing_address=sourcing.address,
            procurement_address=procurement.address,
        ),
        publish_manifest=publish_manifests,
    )
    supplier.include(
        build_supplier_chat_protocol(supplier_service), publish_manifest=publish_manifests
    )
    inventory.include(
        build_inventory_protocol(orchestrator_address=orchestrator.address),
        publish_manifest=publish_manifests,
    )
    rpc_timeout = max(
        60,
        int(
            (http_connect_timeout_seconds + http_read_timeout_seconds) * (http_max_retries + 1) + 10
        ),
    )

    def sourcing_service(ctx: Context) -> SourcingAgentService:
        return SourcingAgentService(
            SupplierAgentClient(ctx, supplier.address, rpc_timeout),
            supplier_ids,
            minimum_valid_quotes=minimum_valid_quotes,
        )

    sourcing.include(
        build_sourcing_protocol(
            sourcing_service,
            LiveFxResolver(
                FrankfurterFxProvider(fx_provider_url, fx_cache_ttl_seconds),
                SqlAlchemyFxRepository(_session_factory),
            ),
            orchestrator_address=orchestrator.address,
        ),
        publish_manifest=publish_manifests,
    )
    procurement_repository = ProcurementRepository(_session_factory)
    journal = SqlAlchemySideEffectJournal(_session_factory)
    recovery_owner = f"procurement-{uuid4()}"

    def procurement_service(ctx: Context) -> ProcurementAgentService:
        service = ProcurementAgentService(
            SupplierAgentClient(ctx, supplier.address, rpc_timeout),
            journal,
            procurement_repository,
            poll_interval_seconds=ack_poll_interval_seconds,
            poll_timeout_seconds=ack_poll_timeout_seconds,
        )
        service.recovery_owner = recovery_owner
        return service

    procurement.include(
        build_procurement_protocol(
            procurement_service,
            recovery_interval_seconds=ack_poll_interval_seconds,
            orchestrator_address=orchestrator.address,
        ),
        publish_manifest=publish_manifests,
    )
    acknowledgment.include(
        build_acknowledgment_protocol(
            AcknowledgmentAgentService(procurement_repository, procurement_repository),
            orchestrator_address=orchestrator.address,
        ),
        publish_manifest=publish_manifests,
    )
    audit_repository = AuditRepository(_session_factory)
    planning_repository = PlanningRequestRepository(_session_factory)
    payments = PaymentRepository(_session_factory)
    payment = None
    if seeds.payment.strip():
        payment = _specialist(
            "supplymesh-payment",
            seeds.payment,
            (
                "Internal specialist: Stripe Checkout for confirmed purchase-order totals. "
                "Called only by the manufacturing Orchestrator."
            ),
            publish_agent_details,
            mailbox=mailbox,
        )
        payment.include(
            build_payment_tool_protocol(
                StripeCheckout(
                    stripe_secret_key,
                    stripe_publishable_key,
                    return_url=stripe_return_url,
                    journal=journal,
                ),
                orchestrator_address=orchestrator.address,
            ),
            publish_manifest=publish_manifests,
        )
        orchestrator.include(
            build_payment_seller_protocol(payment.address, payments),
            publish_manifest=publish_manifests,
        )
    coordinator = OrchestrationCoordinator(
        inventory_address=inventory.address,
        requests=planning_repository,
        workflows=WorkflowRepository(_session_factory, audit_repository),
        sourcing_address=sourcing.address,
        procurement_address=procurement.address,
        acknowledgment_address=acknowledgment.address,
        transition_graph=build_event_transition_graph(),
        audit=audit_repository,
        reservation_store=planning_repository,
        payment_address=payment.address if payment is not None else "",
        payments=payments if payment is not None else None,
    )
    orchestrator.include(build_chat_protocol(coordinator), publish_manifest=publish_manifests)
    orchestrator.include(
        build_planning_response_protocol(coordinator), publish_manifest=publish_manifests
    )
    orchestrator.include(
        build_specialist_response_protocol(coordinator), publish_manifest=publish_manifests
    )
    agents_built = (orchestrator, inventory, sourcing, procurement, acknowledgment, supplier)
    return agents_built if payment is None else (*agents_built, payment)
