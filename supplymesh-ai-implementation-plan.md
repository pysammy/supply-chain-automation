# SupplyMesh AI — Detailed Implementation Plan

Treat this document as the source of truth for architecture, scope, implementation order, and acceptance criteria.

## 1. Project Summary

SupplyMesh AI is an ASI:One-native, multi-agent procurement orchestration system for manufacturing companies.

The demonstration company manufactures motorcycles, but the implementation must use generic product, BOM, inventory, demand, supplier, quotation, purchase-order, and acknowledgment models. Motorcycle-specific information must exist only as seed/demo data. The same software should later support other manufactured products and inventory-driven use cases.

The system must:

1. Accept a manufacturing demand or rush order through ASI:One.
2. Protect inventory already reserved for existing manufacturing orders.
3. Expand the product BOM and calculate additional part requirements.
4. Determine shortages using current inventory, reservations, safety stock, and incoming supply.
5. Request quotations from several supplier REST endpoints.
6. Generate cheapest, fastest, balanced, risk-aware, and split-supplier procurement plans.
7. Present the plans to a human in ASI:One and wait for explicit approval.
8. Generate and send X12 850 purchase orders to the selected supplier REST endpoints.
9. Track supplier processing and retrieve X12 855 acknowledgments.
10. Compare each acknowledgment with the corresponding PO.
11. Complete the happy path automatically when commitments match.
12. Escalate material price, quantity, or delivery conflicts to a human.
13. Allow the human to accept the change, reject it, switch suppliers, split the order, or send a counterproposal.
14. Generate X12 860 change requests for counterproposals and process X12 865 supplier responses.
15. Maintain an auditable, versioned record of every decision and message.

## 2. Locked Architectural Decisions

These decisions are final unless the project owner explicitly changes them:

- Build **five company-side agents**.
- Register all five company-side agents on Agentverse.
- Use Agent Chat Protocol for ASI:One compatibility.
- Use ASI:One as the complete user-facing interface.
- Do not build a React or other custom frontend for the primary workflow.
- Suppliers are **not agents**.
- Suppliers are simulated by one lightweight FastAPI service with multiple supplier-specific REST routes/configurations.
- Use LangGraph in the Orchestrator Agent for controlled state transitions and human approval pauses.
- Use deterministic code for BOM calculations, inventory allocation, optimization, money, EDI generation/parsing, and discrepancy detection.
- Use an LLM only for intent extraction, explanations, summarization, and translating natural-language user actions into validated structured commands.
- Use the ASI:One `asi1` model by default. Make the model configurable through an environment variable.
- Use PostgreSQL for persistent state.
- Use Google OR-Tools for sourcing optimization.
- Use a deliberately scoped X12 subset rather than attempting to implement the entire X12 standard.
- Never allow an LLM to directly send a PO, accept a supplier conflict, or modify inventory without deterministic validation and the required approval policy.

## 3. Mandatory Fetch.ai Outcomes

The completed project must demonstrate all of the following:

- At least one agent registered and discoverable on Agentverse; the target is all five.
- Agent Chat Protocol support.
- Direct use through ASI:One.
- A complete primary workflow inside one ASI:One conversation.
- Meaningful tool/API execution.
- Meaningful multi-agent collaboration.
- Human approvals through ASI:One Interactive Cards.
- A public GitHub repository with setup and test instructions.
- Agentverse profile URLs for submitted agents.
- A public ASI:One shared-chat URL showing the complete workflow.
- A short demo video.
- A concise problem, target-user, and outcome description.

Bonus implementations should include:

- Effective specialist-agent collaboration.
- Interactive Cards.
- Retries, fallbacks, validation, idempotency, and recovery.
- Live supplier capacity data and an optional real-time foreign-exchange provider.
- A generic domain model that can continue beyond the hackathon.

## 4. High-Level Architecture

```mermaid
flowchart TD
    U[Manager in ASI:One] --> O[Manufacturing Orchestrator Agent]
    O --> I[Inventory and BOM Agent]
    O --> S[Sourcing and Optimization Agent]
    O --> P[Procurement Execution Agent]
    O --> E[Acknowledgment and Exception Agent]
    I --> DB[(PostgreSQL)]
    S --> SS[Supplier Simulator REST API]
    P --> SS
    SS --> E
    S --> OPT[OR-Tools and FX Provider]
```

### Deployable services

1. `agent-host`
   - Runs the five company agents, preferably in one Python process using a shared host/Bureau where supported.
   - Gives every agent a stable seed, identity, and Agentverse address.
   - Hosts LangGraph orchestration and domain-tool adapters.

2. `supplier-simulator`
   - Separate FastAPI service.
   - Represents multiple suppliers through configuration.
   - Exposes RFQ, PO, change-order, capacity, and status endpoints.
   - It must not use uAgents and must not be registered on Agentverse.

3. `postgres`
   - Stores business data, workflow checkpoints, approvals, EDI documents, and audit logs.

4. Optional `observability`
   - Use Sentry for error tracking, performance monitoring, and distributed tracing.
   - Instrument both `agent-host` and `supplier-simulator`.
   - Propagate `workflow_id` and `correlation_id` through agent messages and supplier REST calls.
   - Sentry initialization, connectivity, or reporting failures must never block the core workflow.

## 5. Agent Definitions

Every agent must have a unique stable seed supplied through environment variables. Never commit seeds to Git. Every inter-agent request must include a `workflow_id`, `correlation_id`, `message_id`, `schema_version`, `sent_at`, and `reply_to` where applicable.

### 5.1 Manufacturing Orchestrator Agent

Responsibilities:

- Be the main Agentverse-discoverable entry point used from ASI:One.
- Interpret user intent and validate the resulting structured demand.
- Start or resume a LangGraph workflow.
- Delegate BOM/inventory work to the Inventory Agent.
- Delegate quotation and optimization work to the Sourcing Agent.
- Present Interactive Cards and pause for human approval.
- Delegate approved execution to the Procurement Agent.
- Delegate acknowledgment comparison to the Acknowledgment Agent.
- Route exception decisions to the correct next state.
- Give concise status updates and a final outcome.
- Never fabricate supplier quotes or calculation results.

Tools:

- Conversation-intent parser.
- Workflow repository.
- Agent messaging client.
- Interactive Card renderer.
- Audit logger.

### 5.2 Inventory and BOM Agent

Responsibilities:

- Resolve product/version and effective BOM.
- Compute gross component requirements.
- Load on-hand inventory, existing reservations, safety stock, and confirmed inbound supply.
- Preserve reservations belonging to earlier/higher-priority demand orders.
- Create tentative reservations for the new demand.
- Calculate shortages and the earliest feasible production date.
- Return machine-readable calculations and human-readable evidence.

Hard rule:

```text
available_to_promise = on_hand + confirmed_inbound - existing_reservations - safety_stock
gross_required = demand_quantity * bom_quantity_per_product
shortage = max(0, gross_required - max(available_to_promise, 0))
```

All quantities must use `Decimal` or integer minor units where fractional components are allowed. Never use binary floating point for inventory or money.

### 5.3 Sourcing and Optimization Agent

Responsibilities:

- Receive the shortage report.
- Identify suppliers eligible for every missing part.
- Send RFQs concurrently to supplier REST endpoints.
- Enforce per-supplier timeouts.
- Retry safe RFQ requests with exponential backoff.
- Continue with partial results when the minimum quote threshold is met.
- Normalize currencies using the FX provider when required.
- Validate quote expiration, capacity, lead time, and minimum-order constraints.
- Call the deterministic OR-Tools optimizer.
- Generate named procurement alternatives.
- Explain trade-offs without changing optimizer results.

Required plans:

- `cheapest`: minimize landed cost subject to hard constraints.
- `fastest`: minimize latest arrival and total late days.
- `balanced`: weighted cost, lateness, risk, and supplier count.
- `risk_aware`: favor reliable suppliers and delivery confidence.
- `split`: allow quantities to be allocated across suppliers.

### 5.4 Procurement Execution Agent

Responsibilities:

- Accept only approved, version-matched procurement plans.
- Revalidate quote freshness, supplier capacity, totals, and approval hash.
- Generate X12 850 purchase orders.
- Send POs to supplier REST endpoints using idempotency keys.
- Track HTTP acceptance separately from business acknowledgment.
- Poll supplier status routes until an acknowledgment is ready or the timeout policy is reached.
- Forward retrieved X12 855 documents to the Acknowledgment Agent.
- Generate X12 860 change requests for approved counterproposals.
- Retrieve and forward X12 865 responses.
- Maintain immutable PO/change-order versions.
- Never execute without explicit approval, except where the project owner later enables a documented low-risk auto-approval policy.

### 5.5 Acknowledgment and Exception Agent

Responsibilities:

- Parse X12 855 and X12 865 documents.
- Match documents to the correct supplier, PO, PO version, and line item.
- Compare requested and acknowledged quantity, price, date, unit, currency, and acceptance code.
- Classify differences by severity.
- Calculate production, schedule, and cost impact using deterministic services.
- Return `MATCHED` when no material discrepancies exist.
- Create a human exception package when material differences exist.
- Offer valid next actions: accept, counter, switch supplier, split/re-source, or cancel.
- Verify that a human decision still refers to the current PO/version before applying it.

## 6. Internal Message Contracts

Implement contracts as versioned Pydantic models in a shared package. The exact fields may be extended, but existing fields must not be silently renamed after integration begins.

### Common envelope

```python
class MessageEnvelope(BaseModel):
    schema_version: str = "1.0"
    message_id: UUID
    correlation_id: UUID
    workflow_id: UUID
    sender_agent: str
    reply_to: str | None = None
    sent_at: datetime
```

### Required message types

- `DemandPlanningRequest`
- `InventoryShortageReport`
- `SourcingRequest`
- `SupplierQuotesCollected`
- `ProcurementPlansReady`
- `PlanApprovalDecision`
- `PurchaseOrderExecutionRequest`
- `PurchaseOrderSubmitted`
- `AcknowledgmentReceived`
- `AcknowledgmentComparisonResult`
- `ExceptionDecision`
- `CounterproposalExecutionRequest`
- `ChangeOrderSubmitted`
- `ChangeResponseReceived`
- `WorkflowFailure`
- `WorkflowStatusRequest`
- `WorkflowStatusResponse`

Each response must contain structured data. Natural-language explanations are supplemental and must never be the only representation of a business result.

## 7. LangGraph Workflow

Persist checkpoints in PostgreSQL. A workflow must survive a process restart and resume without duplicating RFQs or POs.

### Workflow state

```python
class ProcurementWorkflowState(TypedDict):
    workflow_id: str
    conversation_id: str
    correlation_id: str
    demand_order_id: str | None
    normalized_demand: dict | None
    shortage_report: dict | None
    quotes: list[dict]
    plans: list[dict]
    selected_plan_id: str | None
    approval_id: str | None
    purchase_order_ids: list[str]
    acknowledgments: list[dict]
    exceptions: list[dict]
    current_po_version: int | None
    retry_counts: dict[str, int]
    status: str
    error: dict | None
```

### States and transitions

```mermaid
stateDiagram-v2
    [*] --> Intake
    Intake --> InventoryPlanning
    InventoryPlanning --> Completed: No shortages
    InventoryPlanning --> Sourcing: Shortages exist
    Sourcing --> PlanApproval
    PlanApproval --> Cancelled: Rejected
    PlanApproval --> POExecution: Approved
    POExecution --> AckReview
    AckReview --> Confirmed: Match
    AckReview --> ExceptionDecision: Conflict
    ExceptionDecision --> Confirmed: Accept
    ExceptionDecision --> Counterproposal: Counter
    ExceptionDecision --> Sourcing: Switch or re-source
    Counterproposal --> AckReview
    Confirmed --> Completed
```

### Terminal statuses

- `COMPLETED`
- `CANCELLED`
- `FAILED_REQUIRES_OPERATOR`

### Non-terminal waiting statuses

- `WAITING_FOR_PLAN_APPROVAL`
- `WAITING_FOR_SUPPLIER_ACK`
- `WAITING_FOR_EXCEPTION_DECISION`
- `WAITING_FOR_CHANGE_RESPONSE`
- `RETRY_SCHEDULED`

## 8. Domain Model and Database

Use PostgreSQL, SQLAlchemy 2.x, and Alembic. Include `created_at`, `updated_at`, and appropriate version/audit fields on mutable entities.

### Core tables

#### Product and BOM

- `products`: id, sku, name, version, unit, active.
- `boms`: id, product_id, version, effective_from, effective_to, status.
- `bom_items`: id, bom_id, component_sku, quantity_per, scrap_factor, unit.

#### Demand and inventory

- `demand_orders`: id, external_ref, product_id, quantity, required_by, priority, status.
- `inventory_balances`: component_sku, location, on_hand, safety_stock, version.
- `inventory_reservations`: id, demand_order_id, component_sku, quantity, priority, status.
- `inbound_supply`: id, component_sku, quantity, expected_at, confidence, source_po_id, status.

#### Suppliers and sourcing

- `suppliers`: id, code, name, base_url, currency, status, reliability_score.
- `supplier_parts`: supplier_id, component_sku, unit, minimum_order_quantity, active.
- `rfqs`: id, workflow_id, supplier_id, status, idempotency_key, expires_at.
- `rfq_lines`: rfq_id, line_number, component_sku, requested_quantity, required_by.
- `quotes`: id, rfq_id, supplier_quote_ref, currency, valid_until, status, raw_payload.
- `quote_lines`: quote_id, line_number, component_sku, offered_quantity, unit_price, promised_date, lead_time_days.

#### Optimization and approvals

- `procurement_plans`: id, workflow_id, name, total_cost, currency, latest_arrival, risk_score, status, version.
- `plan_allocations`: plan_id, component_sku, supplier_id, quote_line_id, quantity, unit_price, promised_date.
- `approval_decisions`: id, workflow_id, decision_type, target_type, target_id, target_version, decision, actor, reason, payload_hash, decided_at.

#### Procurement and acknowledgment

- `purchase_orders`: id, po_number, workflow_id, supplier_id, version, currency, status, total, idempotency_key.
- `purchase_order_lines`: po_id, line_number, component_sku, quantity, unit_price, requested_date, unit.
- `edi_documents`: id, workflow_id, supplier_id, document_type, direction, control_number, related_document_id, raw_text, parsed_json, validation_status, created_at.
- `supplier_transactions`: id, supplier_id, transaction_type, external_transaction_id, status, poll_url, next_poll_at, attempts.
- `acknowledgments`: id, po_id, po_version, supplier_ack_ref, acknowledgment_type, status.
- `acknowledgment_lines`: acknowledgment_id, po_line_number, status_code, accepted_quantity, unit_price, promised_date, notes.
- `exceptions`: id, workflow_id, po_id, severity, category, expected_value, actual_value, cost_impact, schedule_impact_days, status.
- `change_orders`: id, po_id, version, status, reason, idempotency_key.

#### Operations and audit

- `workflow_runs`: id, conversation_id, state, status, checkpoint_version, started_at, completed_at.
- `agent_message_log`: id, workflow_id, message_id, sender, recipient, message_type, payload_hash, status, sent_at, received_at.
- `audit_events`: id, workflow_id, actor_type, actor_id, action, entity_type, entity_id, before_json, after_json, created_at.
- `fx_rates`: base_currency, quote_currency, rate, provider, observed_at, expires_at.

### Important constraints

- Unique `(supplier_id, idempotency_key)` for supplier-bound commands.
- Unique `message_id` in `agent_message_log`.
- Unique `(po_number, version)`.
- Optimistic version column on inventory balances.
- Foreign keys must prevent orphaned quote, PO, acknowledgment, and exception lines.
- Raw inbound/outbound EDI documents are immutable.
- Approval records are immutable.

## 9. Supplier Simulator REST API

The Supplier Simulator is an external-system simulation, not an agent.

### Authentication and headers

Require:

- `X-API-Key`
- `Idempotency-Key` for mutating requests
- `X-Correlation-ID`
- `Content-Type: application/json`

Never put secrets in request logs.

### Endpoints

#### Health

```http
GET /health
GET /ready
```

#### Capacity

```http
GET /api/v1/suppliers/{supplier_id}/capacity?component_sku=ENGINE-001
```

Return current capacity, next replenishment, and `observed_at`.

#### RFQ

```http
POST /api/v1/suppliers/{supplier_id}/rfqs
```

Request:

```json
{
  "rfq_id": "uuid",
  "currency": "USD",
  "valid_until": "2026-10-16T20:00:00Z",
  "lines": [
    {
      "line_number": 1,
      "component_sku": "ENGINE-001",
      "quantity": "75",
      "required_by": "2026-12-01"
    }
  ]
}
```

Return a validated quote with supplier reference, offered quantities, prices, promised dates, minimum-order constraints, validity, and optional notes.

#### Purchase order submission

```http
POST /api/v1/suppliers/{supplier_id}/purchase-orders
```

Request envelope:

```json
{
  "message_id": "uuid",
  "document_type": "850",
  "po_number": "PO-2026-0001",
  "po_version": 1,
  "x12": "ISA*...~"
}
```

Return HTTP `202 Accepted`:

```json
{
  "supplier_transaction_id": "SUP-TXN-001",
  "status": "PROCESSING",
  "poll_url": "/api/v1/suppliers/supplier-alpha/purchase-orders/SUP-TXN-001",
  "estimated_ready_at": "2026-10-15T23:10:00Z"
}
```

#### Purchase-order status and acknowledgment

```http
GET /api/v1/suppliers/{supplier_id}/purchase-orders/{supplier_transaction_id}
```

Pending response:

```json
{"status": "PROCESSING", "retry_after_seconds": 2}
```

Completed response:

```json
{
  "status": "ACKNOWLEDGED",
  "document_type": "855",
  "x12": "ISA*...~"
}
```

#### Change-order submission

```http
POST /api/v1/suppliers/{supplier_id}/change-orders
```

Submit a JSON envelope containing an X12 860 document. Return HTTP 202 with a change transaction and poll URL.

#### Change-order status

```http
GET /api/v1/suppliers/{supplier_id}/change-orders/{supplier_transaction_id}
```

Return `PROCESSING` or an X12 865 response.

### Demo-only scenario control

```http
POST /api/v1/demo/reset
POST /api/v1/demo/scenario/{scenario_name}
```

Protect these routes with a separate admin key and disable them in non-demo environments.

Required scenarios:

- `happy_path`
- `price_conflict`
- `quantity_conflict`
- `delivery_conflict`
- `multi_conflict_counter_accepted`
- `counter_rejected`
- `first_call_timeout_then_success`
- `supplier_unavailable`
- `malformed_acknowledgment`

## 10. Supplier Configuration

Store supplier behavior in YAML or JSON. Do not hard-code behavior in route handlers.

```yaml
supplier_id: supplier-alpha
name: Alpha Components
currency: USD
reliability_score: 0.94
response_delay_ms: 700
catalog:
  ENGINE-001:
    unit_price: "820.00"
    available_capacity: "100"
    lead_time_days: 14
    minimum_order_quantity: "10"
ack_behavior:
  mode: accept_exactly
counter_behavior:
  mode: accept_if_margin_at_least
  minimum_margin_percent: "7.5"
```

Create at least three supplier profiles with meaningfully different cost, capacity, lead time, reliability, and acknowledgment behavior.

## 11. EDI Scope

Implement an educational, documented subset sufficient for the demonstration. Preserve raw X12 text and parsed structured JSON.

### X12 850 Purchase Order

Support at minimum:

- `ISA`, `GS`, `ST`
- `BEG`
- `REF`
- `DTM`
- `N1`
- `PO1`
- `CTT`
- `SE`, `GE`, `IEA`

### X12 855 Purchase Order Acknowledgment

Support at minimum:

- Standard envelope segments
- `BAK`
- `REF`
- `PO1`
- `ACK`
- `DTM`

### X12 860 Purchase Order Change Request

Support at minimum:

- Standard envelope segments
- `BCH`
- `REF`
- `POC`
- `DTM`

### X12 865 Purchase Order Change Acknowledgment

Support at minimum:

- Standard envelope segments
- `BCA`
- `REF`
- `POC`
- `ACK`
- `DTM`

### EDI rules

- Generate unique control numbers.
- Verify transaction type and basic envelope consistency.
- Verify segment/transaction counts where implemented.
- Match PO number, version, supplier, currency, and line numbers.
- Preserve unknown segments rather than silently discarding the raw document.
- Reject invalid or mismatched documents into a recoverable exception state.
- Clearly state in the README that this is a scoped hackathon implementation, not a certified production EDI translator.

## 12. Acknowledgment Comparison Rules

Compare every acknowledged line against the latest approved PO version.

Required discrepancy categories:

- `PRICE_CHANGED`
- `QUANTITY_REDUCED`
- `QUANTITY_INCREASED`
- `DELIVERY_DELAYED`
- `DELIVERY_EARLIER`
- `LINE_REJECTED`
- `SUBSTITUTION_PROPOSED`
- `UNIT_CHANGED`
- `CURRENCY_CHANGED`
- `UNKNOWN_LINE`
- `MISSING_LINE`
- `DUPLICATE_ACKNOWLEDGMENT`
- `STALE_PO_VERSION`

Default materiality policy, configurable:

- Any rejected or missing line: critical.
- Any quantity below requested amount: high.
- Delivery later than required-by date: high.
- Price increase greater than 2%: high.
- Price increase at or below 2%: medium and still requires approval for the hackathon demo.
- Earlier delivery or lower price with no other conflict: informational, but record it.

Compute:

- Absolute and percentage cost delta.
- Unfulfilled quantity.
- Production units affected.
- Estimated schedule delay.
- Recommended response options.

The recommendation must be deterministic. The LLM may explain it but must not modify it.

## 13. Optimization Model

Use OR-Tools mixed-integer or constraint programming.

### Decision variable

`x[component, supplier]` = integer or scaled quantity allocated to a supplier.

### Hard constraints

- Total allocated quantity for each component must meet the shortage.
- Allocation cannot exceed offered quantity or current capacity.
- Supplier must be eligible for the component.
- Quote must still be valid.
- Respect minimum-order and packaging multiples.
- For plans claiming on-time feasibility, promised dates must satisfy the required date.

### Objective inputs

- Landed cost.
- Latest promised arrival.
- Total late days.
- Supplier reliability risk.
- Number of suppliers used.
- Optional FX staleness penalty.

Convert monetary values to integer minor units before passing them to the solver.

Return both feasible and infeasible explanations. If no feasible on-time plan exists, return the best attainable plan with explicit unmet constraints; never claim the manufacturing target is achievable.

## 14. ASI:One Interactive Cards

Build cards using Agent Chat Protocol metadata. Every card must also include a useful text fallback.

Required metadata:

```json
{
  "card_protocol_version": "1",
  "requires_card_interaction": "true",
  "card_kind": "review",
  "card_payload": "{...json string...}"
}
```

Implement both response formats:

- Direct-mention structured selection JSON.
- Planner-mediated natural-language selection.

Always attempt strict JSON parsing first. For natural language, use intent extraction followed by Pydantic validation. Never execute an ambiguous selection; ask the user to choose again.

### Required cards

1. `DemandIntakeForm`
   - Product/SKU, quantity, required date, priority, optional cost ceiling.

2. `InventoryAssessmentDetail`
   - Existing reservations, available-to-promise, shortage, and affected components.

3. `ProcurementPlanCarousel`
   - Cheapest, fastest, balanced, risk-aware, and split plans.
   - Show total cost, arrival, suppliers, risk, and late items.

4. `PlanApprovalReview`
   - Approve, modify, reject.
   - Include approval target ID and version in the selection payload.

5. `PurchaseOrderDetail`
   - PO number, supplier, lines, total, dates, and status.

6. `ExceptionReview`
   - Expected versus acknowledged values, cost impact, schedule impact, and severity.
   - Actions: accept, counter, switch supplier, split/re-source, cancel.

7. `CounterproposalForm`
   - Acceptable quantity, maximum price, required date, and note.

8. `CompletionDetail`
   - Confirmed commitments, target feasibility, total cost, remaining risks, and audit references.

9. `AgentActivityDetail`
   - A concise trace of which specialist agent performed each operation and the outcome.

Keep card payloads below platform limits and do not expose secrets or full internal stack traces.

## 15. Human Approval and Governance

Require explicit human approval for:

- Selecting a procurement plan.
- Sending initial purchase orders.
- Accepting any material supplier discrepancy.
- Sending a counterproposal.
- Switching or splitting suppliers after an exception.
- Cancelling an active procurement workflow.

For every approval:

- Persist the target entity ID and version.
- Persist a SHA-256 hash of the approved payload.
- Persist actor, timestamp, action, and optional reason.
- Recalculate/hash the payload immediately before execution.
- Reject stale approval if the plan or PO changed.

## 16. Reliability and Recovery

Implement the following as core functionality, not documentation-only claims:

### Idempotency

- Derive stable keys for RFQ, PO, and change-order submissions.
- Repeated submissions with the same key return the original transaction.
- Never create a second PO due to a retry.

### Timeouts and retries

- Use per-request connection/read timeouts.
- Retry only safe/idempotent calls.
- Use exponential backoff with jitter and a strict maximum attempt count.
- Honor supplier `retry_after_seconds` within configured limits.

### Partial results

- Sourcing can proceed when at least the configured number of valid supplier quotes exists.
- Clearly identify missing suppliers.
- If no feasible plan exists, escalate instead of fabricating one.

### Duplicate and stale messages

- Deduplicate by `message_id`, idempotency key, supplier transaction ID, document control number, and PO version as appropriate.
- Quarantine stale or future-version acknowledgments.

### Checkpointing

- Persist after every external side effect and human decision.
- Resume polling after restart.
- Do not repeat completed side effects during replay.

### Compensation

- Release tentative inventory reservations if a workflow is cancelled before commitment.
- Mark reservations as committed only when the required supply is confirmed.
- Preserve the audit trail even after compensation.

### Fallbacks

- Cache the last successful FX rate with timestamp.
- If the live provider fails, use a non-expired cached rate and disclose it.
- If all supplier calls fail, offer retry or manual intervention.

## 17. Real-Time External Data Bonus

Implement provider interfaces so external services can be replaced.

### Live supplier data

- Supplier capacity endpoints are the primary real-time source.
- Include `observed_at` and refuse data older than the configured threshold.

### Foreign exchange

- Add an `FxRateProvider` interface.
- Use a no-key public provider by default where practical.
- Cache successful rates in PostgreSQL.
- Include provider, observed time, and whether the value is live or cached in the plan.
- Configure one demo supplier to quote in EUR so FX affects plan comparison.

Do not add additional public APIs until the full core workflow is stable.

## 18. Observability and Auditability

Use Sentry as the only external observability platform. OpenTelemetry is not required for this project.

### Sentry integration

- Initialize the Sentry Python SDK in both `agent-host` and `supplier-simulator` when `SENTRY_DSN` is configured.
- Enable the FastAPI/Starlette integration and outgoing HTTP client instrumentation.
- Capture unhandled exceptions and explicitly capture important recovered failures such as exhausted supplier retries or invalid EDI documents.
- Create transactions/spans for complete procurement workflows, agent operations, optimization, supplier API calls, acknowledgment polling, and EDI parsing.
- Attach `workflow_id`, `correlation_id`, agent name, supplier ID, PO number/version, and workflow state as Sentry tags or structured context where applicable.
- Propagate Sentry trace information across supplier REST calls when supported. Always propagate the application's `workflow_id` and `correlation_id`, including across agent messages, so events remain searchable even where automatic trace propagation is unavailable.
- Configure sample rates through environment variables rather than hard-coding them.
- If the DSN is absent or Sentry initialization/reporting fails, log a local warning and continue normally.
- Never include agent seeds, API keys, raw authorization headers, or sensitive payloads in Sentry events.

### Structured logging

Every log should include where applicable:

- timestamp
- level
- service
- agent name
- workflow ID
- correlation ID
- message ID
- supplier ID
- PO number/version
- operation
- duration
- outcome

Redact API keys, agent seeds, and personal data.

### Performance signals

- Record workflow duration through Sentry transactions.
- Record agent-message and supplier-request latency through spans.
- Add measurements or structured context for quote count, partial-quote count, retry count, acknowledgment duration, exception severity, optimization duration, and solver feasibility.
- Do not introduce a separate metrics collector for the hackathon build.

### Tracing

Use Sentry tracing where automatic propagation is supported, and always propagate the application correlation ID through:

```text
ASI:One -> Orchestrator -> Specialist Agent -> Supplier API -> Specialist Agent -> Orchestrator
```

### Audit trail

Record every material state transition, external side effect, approval, EDI document, and exception decision.

## 19. Security

- Store secrets in environment variables or the deployment secret manager.
- Commit only `.env.example` with placeholders.
- Use stable but secret agent seeds.
- Require API keys between `agent-host` and `supplier-simulator`.
- Validate all inbound payloads with Pydantic.
- Limit payload sizes.
- Use allow-listed supplier base URLs; do not accept arbitrary URLs from users.
- Use parameterized database access through SQLAlchemy.
- Avoid rendering raw supplier HTML.
- Never log raw authorization headers, agent seeds, or API tokens.
- Use HTTPS for deployed public endpoints.
- Rate-limit demo/admin endpoints.
- Ensure demo reset cannot run in production mode.

## 20. Suggested Repository Layout

```text
supplymesh-ai/
├── README.md
├── CONTRIBUTING.md
├── LICENSE
├── pyproject.toml
├── uv.lock                         # or requirements lockfile
├── .env.example
├── .gitignore
├── docker-compose.yml
├── Makefile
├── alembic.ini
├── migrations/
├── apps/
│   ├── agent_host/
│   │   ├── main.py
│   │   ├── settings.py
│   │   └── health.py
│   └── supplier_simulator/
│       ├── main.py
│       ├── routes/
│       ├── services/
│       └── settings.py
├── supplymesh/
│   ├── agents/
│   │   ├── orchestrator.py
│   │   ├── inventory.py
│   │   ├── sourcing.py
│   │   ├── procurement.py
│   │   └── acknowledgment.py
│   ├── protocols/
│   │   ├── common.py
│   │   ├── planning.py
│   │   ├── sourcing.py
│   │   ├── procurement.py
│   │   └── exceptions.py
│   ├── workflows/
│   │   ├── procurement_graph.py
│   │   ├── state.py
│   │   └── nodes/
│   ├── domain/
│   │   ├── models.py
│   │   ├── enums.py
│   │   └── policies.py
│   ├── services/
│   │   ├── bom_service.py
│   │   ├── inventory_service.py
│   │   ├── reservation_service.py
│   │   ├── optimizer.py
│   │   ├── comparison_service.py
│   │   ├── impact_service.py
│   │   └── fx_service.py
│   ├── edi/
│   │   ├── parser.py
│   │   ├── validator.py
│   │   ├── x12_850.py
│   │   ├── x12_855.py
│   │   ├── x12_860.py
│   │   └── x12_865.py
│   ├── cards/
│   │   ├── renderer.py
│   │   ├── demand.py
│   │   ├── plans.py
│   │   ├── exception.py
│   │   └── completion.py
│   ├── persistence/
│   │   ├── database.py
│   │   ├── orm_models.py
│   │   └── repositories/
│   ├── clients/
│   │   ├── supplier_client.py
│   │   ├── asi1_client.py
│   │   └── fx_client.py
│   └── observability/
│       └── sentry.py
├── config/
│   ├── suppliers/
│   │   ├── alpha.yaml
│   │   ├── beta.yaml
│   │   └── gamma.yaml
│   └── policies.yaml
├── data/
│   ├── motorcycle_bom.json
│   ├── initial_inventory.json
│   └── existing_orders.json
├── scripts/
│   ├── seed_demo.py
│   ├── reset_demo.py
│   ├── register_agents.py
│   └── run_demo_scenario.py
├── examples/
│   ├── x12/
│   └── card_payloads/
└── tests/
    ├── unit/
    ├── contract/
    ├── integration/
    ├── e2e/
    └── fixtures/
```

## 21. Environment Variables

Provide all of these in `.env.example` without real values:

```dotenv
APP_ENV=development
LOG_LEVEL=INFO
DATABASE_URL=postgresql+psycopg://user:password@postgres:5432/supplymesh

ASI_ONE_API_KEY=
ASI_ONE_MODEL=asi1
AGENTVERSE_API_KEY=

ORCHESTRATOR_AGENT_SEED=
INVENTORY_AGENT_SEED=
SOURCING_AGENT_SEED=
PROCUREMENT_AGENT_SEED=
ACKNOWLEDGMENT_AGENT_SEED=

SUPPLIER_SIMULATOR_BASE_URL=http://supplier-simulator:8001
SUPPLIER_API_KEY=
SUPPLIER_ADMIN_API_KEY=

FX_PROVIDER_URL=
FX_CACHE_TTL_SECONDS=3600

HTTP_CONNECT_TIMEOUT_SECONDS=3
HTTP_READ_TIMEOUT_SECONDS=10
HTTP_MAX_RETRIES=3
ACK_POLL_INTERVAL_SECONDS=2
ACK_POLL_TIMEOUT_SECONDS=45

SENTRY_DSN=
SENTRY_TRACES_SAMPLE_RATE=1.0
SENTRY_PROFILES_SAMPLE_RATE=0.0
```

## 22. Seed Demonstration Data

Create one product named `UrbanX Motorcycle`, SKU `MOTO-URBANX-2026`.

Use a manageable BOM of approximately 8–12 components, for example:

- Engine assembly
- Frame
- Battery
- Front wheel
- Rear wheel
- Brake kit
- ECU
- Fuel tank
- Headlight assembly
- Wiring harness

Seed:

- An existing quarterly demand of 100 motorcycles.
- Inventory/reservations sufficient or mostly sufficient for the existing demand.
- A rush order of 75 additional motorcycles.
- Shortages for 3–5 components after existing reservations are protected.
- Three suppliers with overlapping catalogs and different trade-offs.

The preferred primary demo should produce multiple quotations, a split/balanced recommended plan, at least one exact acknowledgment, and at least one acknowledgment with price/quantity/date conflicts followed by an accepted counterproposal.

## 23. Test Strategy

Use `pytest`. Target at least 80% coverage for deterministic domain services; do not chase coverage in framework glue at the expense of end-to-end reliability.

### Unit tests

- BOM expansion.
- Scrap-factor calculation.
- Available-to-promise calculation.
- Reservation priority.
- Shortage calculation.
- Currency conversion and stale-rate handling.
- Every optimizer objective and hard constraint.
- EDI generation and parsing.
- PO/ack comparison.
- Materiality/severity rules.
- Impact calculation.
- Idempotency-key generation.
- Approval hash/version validation.
- Card payload generation and size validation.

### Contract tests

- RFQ request/quote response schemas.
- PO submission and polling.
- Change-order submission and polling.
- Authentication and idempotency behavior.
- Supplier timeout and malformed response handling.

### Agent integration tests

- Orchestrator to Inventory Agent.
- Orchestrator to Sourcing Agent.
- Orchestrator to Procurement Agent.
- Procurement to Acknowledgment Agent.
- Duplicate message handling.
- Correlation-ID propagation.

### End-to-end tests

1. Enough inventory; no sourcing required.
2. Shortage; quotes; approval; exact 855; completion.
3. Shortage; price/date conflict; human accepts.
4. Shortage; conflict; 860 counterproposal; accepted 865.
5. Conflict; switch supplier; new sourcing and PO.
6. One supplier times out; partial quotes still produce a valid plan.
7. All suppliers fail; workflow pauses safely.
8. Duplicate PO submission does not create a duplicate order.
9. Duplicate/stale acknowledgment is quarantined.
10. Process restarts while waiting for acknowledgment and resumes correctly.

### ASI:One/manual acceptance tests

- Agent is discoverable by relevant queries, not only direct address.
- Direct `@mention` works.
- Planner-routed invocation works.
- Interactive Cards render.
- JSON card replies work.
- Natural-language card replies work.
- Full primary workflow completes in one shareable ASI:One conversation.

## 24. Implementation Phases

The coding agent must implement in this order and keep the repository runnable after each phase.

### Phase 0 — Foundation

- Initialize Python project, formatting, linting, typing, tests, Docker Compose, settings, database, and migrations.
- Add health/readiness routes.
- Add common IDs, envelopes, error types, and structured logging.
- Add `.env.example` and secret-safe `.gitignore`.

Exit criteria: services start through Docker Compose, database migration succeeds, health checks pass, CI runs.

### Phase 1 — Fetch.ai connectivity spike

- Create Orchestrator Agent.
- Implement Agent Chat Protocol.
- Register it on Agentverse.
- Verify ASI:One discovery and a basic reply.
- Render one basic Interactive Card and process its response.

Exit criteria: a real ASI:One conversation reaches the local/deployed Orchestrator and completes a card interaction.

Do this early because it is the highest-risk external integration.

### Phase 2 — Domain and inventory planning

- Implement product/BOM/demand/inventory schema.
- Implement seed data.
- Implement reservation and shortage services.
- Implement Inventory Agent and typed messages.
- Connect Orchestrator to Inventory Agent.

Exit criteria: rush order produces a tested shortage report that preserves existing reservations.

### Phase 3 — Supplier simulator and sourcing

- Implement configurable supplier profiles.
- Implement capacity and RFQ endpoints.
- Implement Sourcing Agent.
- Add concurrent requests, validation, timeouts, retry, and partial results.
- Implement OR-Tools plans.
- Add plan comparison and approval cards.

Exit criteria: ASI:One displays several deterministic procurement plans based on actual supplier API calls.

### Phase 4 — Purchase-order execution

- Implement scoped X12 850 generator.
- Implement supplier PO acceptance and asynchronous-style polling.
- Implement Procurement Agent, approval verification, idempotency, and PO versions.
- Implement X12 855 generation/parsing.

Exit criteria: approved plan creates one or more POs and retrieves acknowledgments without duplicate side effects.

### Phase 5 — Exceptions and negotiation

- Implement comparison, materiality, and impact services.
- Implement Acknowledgment Agent.
- Implement exception and counterproposal cards.
- Implement X12 860/865 flow.
- Implement accept, counter, switch, split/re-source, and cancel transitions.

Exit criteria: primary conflict scenario reaches a final confirmed state after a human-approved counterproposal.

### Phase 6 — Reliability and bonus features

- Add checkpoint/restart tests.
- Add controlled supplier failure scenarios.
- Add live FX with cache/fallback.
- Complete metrics/traces/activity card.
- Register all five agents and improve discovery metadata.

Exit criteria: one visible failure recovers during the demonstration and bonus integrations cannot break the core flow.

### Phase 7 — Submission polish

- Run full automated and manual test suites.
- Produce one clean ASI:One shared conversation.
- Finalize README and Agentverse profiles.
- Record the demo video.
- Verify the public repository contains no secrets.

## 25. CI and Code Quality

Configure CI to run:

- Formatter check (`ruff format` or equivalent).
- Linter (`ruff`).
- Static typing (`mypy` or `pyright`) for domain/protocol packages.
- Unit and integration tests.
- Migration validation.
- Secret scan if readily available.

Use Python 3.11 or another version supported by all selected Fetch.ai packages. Pin dependencies only after verifying compatibility with the current official Fetch.ai documentation.

## 26. Deployment Plan

### Development

- Docker Compose for PostgreSQL, agent host, and supplier simulator.
- Use a secure public tunnel only for early Agentverse connectivity testing.

### Demo deployment

- Deploy `agent-host` and `supplier-simulator` to stable HTTPS URLs.
- Use managed PostgreSQL or a persistent hosted instance.
- Keep agent seeds and API keys in the hosting provider's secret manager.
- Configure health checks and automatic restart.
- Keep the deployment active throughout judging.
- Validate Agentverse profiles and ASI:One discovery after every deployment change.

### Agentverse profile requirements

For every company agent, provide:

- Clear name and single-sentence capability.
- Relevant discovery keywords.
- Inputs and outputs.
- Example user queries.
- Tools/actions performed.
- Limitations and approval requirements.
- Endpoint health.

Suggested keywords for the Orchestrator:

```text
manufacturing procurement, inventory planning, BOM, component shortage,
supplier sourcing, RFQ, purchase order, supply chain, EDI, X12,
supplier acknowledgment, procurement optimization
```

## 27. README Requirements

The public README must contain:

1. Project description and problem statement.
2. Why this is an agentic system rather than a chatbot.
3. Architecture diagram.
4. Agent responsibility table.
5. Explicit statement that suppliers are REST endpoints, not agents.
6. Full workflow explanation.
7. Technology stack.
8. Local setup instructions.
9. Environment-variable reference.
10. Database migration and seed instructions.
11. Test commands.
12. Exact demo prompt.
13. ASI:One shared-chat URL.
14. Agentverse profile URL for every submitted agent.
15. Demo video link.
16. Sample EDI documents.
17. Reliability/error-handling discussion.
18. Security notes.
19. Limitations and future ERP/WMS/real-EDI integrations.

## 28. Demo Script

Recommended user prompt:

> We received a rush order for 75 UrbanX motorcycles required by December 1. Protect inventory already reserved for existing orders, determine any shortages, get supplier quotations, and recommend a procurement plan.

Expected demonstration:

1. Orchestrator validates demand in an Interactive Card.
2. Inventory Agent shows protected reservations and shortages.
3. Sourcing Agent calls three supplier REST endpoints.
4. ASI:One displays multiple plan cards.
5. Human approves the balanced/split plan.
6. Procurement Agent creates and sends X12 850 POs.
7. One 855 matches and another contains material conflicts.
8. Exception card clearly shows price, quantity, and delivery impact.
9. Human sends a counterproposal.
10. Procurement Agent sends X12 860.
11. Supplier REST service returns X12 865 accepting it.
12. Completion card confirms final supply commitments and manufacturing feasibility.
13. Agent Activity card proves multi-agent collaboration and recovery behavior.

Prepare a short happy-path variant for judge questions.

## 29. Acceptance Criteria / Definition of Done

The project is complete only when all of these are true:

### Functional

- [ ] Existing reservations are preserved when a new demand arrives.
- [ ] BOM and shortage calculations are deterministic and tested.
- [ ] At least three supplier configurations return distinct quotations through REST.
- [ ] Optimization returns multiple valid plans and explains infeasibility honestly.
- [ ] Human approval is required before PO execution.
- [ ] X12 850 is generated, stored, and sent.
- [ ] X12 855 is retrieved, parsed, and compared.
- [ ] Matching acknowledgment reaches the happy-path terminal state.
- [ ] Conflicting acknowledgment creates a clear exception.
- [ ] Human can accept, counter, switch/re-source, or cancel.
- [ ] X12 860/865 counterproposal loop works.
- [ ] Duplicate PO calls do not create duplicate supplier orders.
- [ ] Workflow resumes safely after restart.

### Agentic and Fetch.ai

- [ ] Five distinct company agents exist with clear responsibilities.
- [ ] Suppliers remain REST services and are not implemented as agents.
- [ ] All five company agents have stable identities and Agentverse profiles.
- [ ] Orchestrator is discoverable and usable through ASI:One.
- [ ] Agent Chat Protocol works.
- [ ] Specialist agents exchange typed messages.
- [ ] Interactive Cards work for intake, plan approval, exception handling, and completion.
- [ ] Complete primary workflow is demonstrated inside ASI:One.
- [ ] Shared ASI:One conversation URL is available.

### Reliability

- [ ] Timeouts, retries, and partial supplier responses are handled.
- [ ] Quote/approval/PO versions are validated.
- [ ] Correlation IDs are present end to end.
- [ ] External side effects are idempotent.
- [ ] Failures produce actionable user messages without exposing stack traces.
- [ ] Audit history exists for messages, approvals, EDI, and state transitions.

### Bonus and submission

- [ ] Agent Activity card visibly proves collaboration.
- [ ] Live FX/capacity data has cache/fallback behavior.
- [ ] Public GitHub repository has complete run/test instructions.
- [ ] Agentverse URLs, ASI:One URL, demo video, and project description are ready.
- [ ] No credentials, agent seeds, or API keys are committed.

## 30. Non-Goals and Scope Protection

Do not implement these until every acceptance criterion for the core workflow passes:

- A React dashboard or custom user frontend.
- Supplier agents.
- Real supplier email integration.
- Connection to a commercial EDI value-added network.
- Full X12 standard coverage.
- ERP-specific SAP/Oracle/NetSuite connectors.
- Payment Protocol, wallets, tokens, or settlement of component invoices.
- Machine-learning training or fine-tuning.
- Autonomous approval of material conflicts.
- Unbounded agent loops.

