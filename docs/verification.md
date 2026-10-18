# Verification and Definition of Done Status

Last verified: 2026-10-18 (America/Los_Angeles)

This matrix separates executed evidence from code presence and external/manual blockers. A check
means the cited automated test or reproducible command passed; unchecked items are not claimed.

## Executed locally

- [x] Ruff formatting and linting: `uv run ruff format --check . && uv run ruff check .`
- [x] Strict typing for 48 agent/domain/persistence/protocol/service source files:
  `.venv/bin/mypy`.
- [x] Automated suite: 185 passed with `.venv/bin/python -m pytest -q
  --cov=supplymesh --cov=apps --cov-report=term:skip-covered`.
- [x] Exception cards identify the supplier and PO, and map each discrepancy's PO line number to
  its component SKU. Pending exception cards can be redisplayed without changing workflow state.
- [x] Inventory assessment rounds aggregated EA requirements up and available whole-part counts
  down before reserving stock. Fractional material units retain their precision; card quantities
  omit trailing decimal zeros.
- [x] Inventory assessment remains visible until the user continues. Sourcing dispatch is gated
  by conversation, workflow ID, review version, and duplicate-click checks. Sufficient inventory
  presents View completion without immediately replacing the assessment.
- [x] Supplier REST quotations cover every component in the demo motorcycle BOM; a regression
  sources 20 of each component and verifies all five plans cover every shortage.
- [x] Full `supplymesh` plus `apps` source coverage: 90% (`pytest-cov`).
- [x] Supplier history: new POs/changes persist manufacturer identity, original terms and supplier
  responses. Restart/reset replay, filters, pagination, changed-term rejection, atomic failure,
  and direct public Supplier history cards are covered through real HTTP/protocol handlers.
- [x] Migration `0008` upgrade/downgrade/re-upgrade and schema parity passed in an isolated
  PostgreSQL database. Concurrent identical submissions returned one saved transaction; restart
  and scenario reset retained history and acknowledgment status. The demo database was untouched.
- [ ] New supplier history cards rendered live in ASI:One after deploying migration `0008` and
  rebuilding the supplier simulator and agent host. See [supplier history](supplier-history.md).
- [x] Optional payment collection: mocked Stripe checks cover explicit checkout approval,
  restart recovery, duplicate confirmations, lost callbacks, amount/currency/workflow validation,
  unauthorized requests, expired sessions, and database failure before receipt confirmation.
- [x] Isolated PostgreSQL payment checks: migrations through `0007`, JSON session lookup,
  conversation isolation, atomic receipt/audit rollback, durable receipts, and duplicate
  confirmation. `alembic check` reports no schema changes; no payment migration is required.
- [x] Corrected Stripe client created and retrieved an actual test-mode Checkout Session, both
  HTTP 200, with API version `2026-09-30.endive`. The session is journaled for reuse; no payment
  was submitted. Request schema constraints are recorded from Stripe's published OpenAPI source.
- [x] Native embedded checkout payload accepted by ASI:One's actual production chat parser,
  executed locally from its public bundle. Native chat callbacks traverse the Orchestrator
  handler and require conversation ownership and successful Stripe verification before collection.
- [ ] Live payment UI and callback confirmation. The latest compatibility fix requires rebuilding
  the running agent host; see [payment setup and verification](payments.md).
- [x] PostgreSQL migrations `0001`–`0007` upgrade, downgrade to base, and re-upgrade on a clean
  PostgreSQL 17 database.
- [x] Alembic reports no SupplyMesh schema drift after LangGraph checkpoint setup.
- [x] PostgreSQL checkpoint restart: pause, close graph/saver, reopen/resume twice, complete.
- [x] PostgreSQL side-effect journal: pending and success survive independent sessions.
- [x] Six unique stable uAgent addresses derive from six distinct runtime-only seeds.
- [x] Demand card submission traverses registered Orchestrator and Inventory uAgent protocol
  handlers, persists the demand/workflow before dispatch, and returns a correlated assessment card.
- [ ] Docker Compose service startup. `docker compose config --quiet` passed, but the 2026-10-17
  image pull stalled in `docker-credential-desktop`; no service was created and the attempt was
  stopped and cleaned up.

## Functional

- [x] Existing reservations are preserved in shortage calculations.
- [x] BOM, scrap factor, available-to-promise, tentative reservation, and shortage math are tested.
- [x] Three configuration-driven suppliers return distinct REST quotes.
- [x] OR-Tools returns five named plans and reports infeasibility without fabrication.
- [x] Separate human approvals gate plan selection and PO sending in the workflow.
- [x] X12 850 generation, supplier submission, idempotency, and 855 polling are tested.
- [x] Exact 855 reaches the matching/completion path.
- [x] Conflicting 855 creates typed severity, cost, quantity, production, and schedule impact.
- [x] Runtime exception actions support accept, counter, and cancel. Switch/split replacement
  requires operator review and is not offered on demo cards.
- [x] Human-approved X12 860 and accepted 865 loop is tested end to end.
- [x] Duplicate PO calls return the original supplier transaction.
- [x] Workflow resumes from PostgreSQL after graph/checkpointer reconstruction.
- [x] File-backed restart tests close the database after supplier acceptance but before local
  submission state is recorded, then reconstruct the Procurement Agent and finish 850/855 and
  860/865 exactly once without a duplicate supplier submission.
- [x] Recovery after acknowledgment persistence replays the stored 855/865 with the same message
  IDs without polling or submitting again; active database leases exclude a second worker.
- [x] Tentative reservations persist idempotently and transition once to committed on confirmed
  supply or released on cancellation.
- [x] Raw and parsed EDI, acknowledgment lines, discrepancies, and change-order outcomes are
  persisted idempotently as structured procurement evidence; malformed inbound EDI is retained as
  invalid evidence instead of being discarded.

## Agentic and Fetch.ai

- [x] Five distinct company-side uAgent identities and responsibilities exist.
- [x] One Supplier Agent fronts three supplier REST profiles; only it calls supplier backend tools.
- [x] Only Orchestrator and Supplier expose Chat Protocol; specialists enforce Orchestrator sender
  addresses and Supplier tools enforce Sourcing/Procurement roles.
- [x] Local uAgents dispatcher round trips exercise three supplier quotes, capacity, duplicate PO
  submission, 850/855, and 860/865 through the Supplier Agent and real in-process FastAPI routes.
- [x] Public Supplier chat allows quotation/capacity only; unauthorized tools and mismatched replies
  are rejected.
- [x] Agent Chat Protocol 0.3.0 manifest and card metadata build against installed official models.
- [x] All six runtime names validate against the installed SDK's Agentverse registration model
  (32-character limit); seeds and address derivation are unchanged.
- [x] Typed versioned specialist request/result models and in-process service adapters are covered.
- [x] Card builders use documented predefined schemas and custom element trees. Flat demand and
  supplier form replies traverse message handlers; counterproposal target/version parsing is tested.
  Live rendering remains unverified; see [manual card verification](card-verification.md).
- [x] The Fetch.ai connectivity vertical slice sends a Pydantic-2-validated planning contract over
  uAgents wire models, protects existing reservations, rejects envelope tampering, and deduplicates
  repeated demand-card message IDs.
- [x] Inventory, sourcing, procurement, and acknowledgment specialists publish real typed uAgent
  handlers. Focused integration tests cover quotes/plans, approval-gated 850/855, approved 860/865,
  exact acknowledgment comparison, malformed EDI, correlation, and bounded failures.
- [x] Orchestrator ACP card handlers drive Inventory, Sourcing, separate plan/PO approvals,
  Procurement, Acknowledgment, exact completion, exception review, and the approved 860/865 loop;
  every callback is checked by the LangGraph event-transition reducer.
- [x] Orchestrator and all specialists exchange typed uAgents contracts in handler-level integration
  tests with end-to-end correlation/reply validation and duplicate inbound suppression.
- [x] Pending Orchestrator-to-specialist messages retain their validated typed payload and replay
  with the original message ID after restart.
- [x] Workflow completion sends one card combining totals, commitments, and a compact activity
  summary from persisted message and state-transition evidence. Full audit rows remain stored.
- [ ] All six agents registered with stable Agentverse profile URLs.
- [ ] Orchestrator and Supplier discoverable and directly usable through ASI:One.
- [ ] Direct mention and planner-routed invocation verified live.
- [ ] Interactive Card rendering and both reply formats verified live.
- [ ] Complete primary workflow demonstrated in one ASI:One conversation.
- [ ] Shared ASI:One conversation URL available.

External blocker: live Agentverse and ASI:One checks require credentials, deployed HTTPS endpoints,
and human interaction in those products. No credentials or URLs were present in the workspace.

## Reliability and submission

- [x] Timeout/retry/partial-result behavior and controlled first-call recovery are tested.
- [x] Approval/PO versions, correlation IDs, stable keys, and stale decisions are validated.
- [x] External PO/change-order side effects use pre/post durable journal records.
- [x] PO/change-order intent is persisted before the supplier call; incomplete `SUBMITTING` and
  `PROCESSING` transactions are reconciled by the Procurement Agent's recovery interval.
- [x] Durable agent-message delivery status and append-only material state-transition audit events
  persist to PostgreSQL; message IDs are unique for deduplication and pending payloads are replayable.
- [x] Failures produce bounded public errors without raw stack traces.
- [x] FX has PostgreSQL cache and non-expired fallback semantics.
- [x] Live Frankfurter v2 lookup returned EUR/USD `1.122` dated 2026-10-16; automated tests cover
  cached fallback and expired-cache refusal.
- [x] Public-repository setup, migration, test, architecture, security, and limitation docs exist.
- [ ] Live Agent Activity card rendered in ASI:One.
- [ ] Public repository remote content independently verified after push.
- [ ] Agentverse URLs, ASI:One URL, and demo video recorded.
- [x] Repository secret check scans tracked and non-ignored untracked text for populated credential
  variables, agent seeds, and private-key blocks; it passes locally and runs in CI.
- [x] `detect-secrets 1.5.0` scan executed; findings were reviewed as documented placeholders,
  local-only database passwords, test keys, and generated cache tags. No real credential, private
  key, `.env`, or agent seed file was present.

## Reproducible PostgreSQL restart check

1. Create a disposable database and run `alembic upgrade head`.
2. Run `scripts/setup_checkpoints.py` with `LANGGRAPH_STRICT_MSGPACK=true`.
3. Compile with `PostgresSaver`, invoke until `WAITING_FOR_PLAN_APPROVAL`, and close it.
4. Open a new saver/graph with the same `thread_id`, resume plan approval, and close it.
5. Open a third saver/graph, resume PO approval, and assert `COMPLETED`.
6. Run `alembic check`; framework checkpoint tables are intentionally excluded by name.
