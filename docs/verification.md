# Verification and Definition of Done Status

Last verified: 2026-10-17 (America/Los_Angeles)

This matrix separates executed evidence from code presence and external/manual blockers. A check
means the cited automated test or reproducible command passed; unchecked items are not claimed.

## Executed locally

- [x] Ruff formatting and linting: `uv run ruff format --check . && uv run ruff check .`
- [x] Strict typing for 43 agent/domain/persistence/protocol/service source files:
  `uv run --python 3.11 --isolated mypy`.
- [x] Automated suite: 74 passed with `uv run pytest -q --cov=supplymesh --cov=apps`.
- [x] Full `supplymesh` plus `apps` source coverage: 89% (`pytest-cov`).
- [x] PostgreSQL migrations `0001`–`0007` upgrade, downgrade to base, and re-upgrade on a clean
  PostgreSQL 17 database.
- [x] Alembic reports no SupplyMesh schema drift after LangGraph checkpoint setup.
- [x] PostgreSQL checkpoint restart: pause, close graph/saver, reopen/resume twice, complete.
- [x] PostgreSQL side-effect journal: pending and success survive independent sessions.
- [x] Five unique stable uAgent addresses derive from five distinct runtime-only seeds.
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
- [x] Exception transitions support accept, counter, switch/re-source, split/re-source, and cancel.
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
- [x] Suppliers are FastAPI REST routes and not agents.
- [x] Agent Chat Protocol 0.3.0 manifest and card metadata build against installed official models.
- [x] Typed versioned specialist request/result models and in-process service adapters are covered.
- [x] Intake, plan, approval, exception, completion, and activity card payloads exist and validate.
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
- [x] Workflow completion builds and sends an Agent Activity card from bounded persisted message
  and state-transition evidence.
- [ ] All five agents registered with stable Agentverse profile URLs.
- [ ] Orchestrator discoverable and directly usable through ASI:One.
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
