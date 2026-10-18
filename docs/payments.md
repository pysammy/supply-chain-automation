# Optional Stripe payments

The internal `supplymesh-payment` agent creates and verifies Stripe embedded Checkout sessions.
The Orchestrator publishes Agent Payment Protocol 0.1.0 as the seller. Only the Orchestrator and
Supplier publish Agent Chat Protocol; the Payment Agent accepts typed requests only from the
Orchestrator.

Payments collect the final confirmed PO total into the configured Stripe merchant account.
They do not pay the simulated suppliers. Supplier payouts, refunds, and Stripe Connect are outside
this implementation. Procurement finishes and reservations are committed before payment is offered.

## Configuration

Payments are disabled when `PAYMENT_AGENT_SEED` is blank. Existing six-agent deployments continue
to work. To enable payment testing, configure these values in the private `.env`:

| Variable | Purpose |
|---|---|
| `PAYMENT_AGENT_SEED` | A stable seventh seed, distinct from the existing six |
| `STRIPE_SECRET_KEY` | Stripe test-mode secret API key |
| `STRIPE_PUBLISHABLE_KEY` | Matching Stripe test-mode publishable key |
| `STRIPE_RETURN_URL` | Accessible URL after payment authentication; defaults to `https://asi1.ai` |

The seed enables the feature; both Stripe keys are then required at startup. Keys alone do not
enable it. Use test-mode credentials for the demo. The application does not enforce test-mode key
prefixes. Checkout currently supports USD and EUR, with amounts greater than zero and at most
99,999,999 minor units. Other procurement currencies are not supported by this checkout client.
Available payment methods come from the Stripe Dashboard settings; checkout creation does not
send the deprecated `payment_method_types` parameter.

Stripe requests explicitly use `Stripe-Version: 2026-09-30.endive`, the current documented
version checked on 2026-10-18. Both session creation and retrieval use that header. The API request
uses `ui_mode=embedded_page`. The separate Fetch client metadata retains `ui_mode=embedded`, as
shown in Fetch's examples; that client label is never sent as a Stripe API parameter.

After configuring secrets, rebuild the agent host. Register the Payment Agent mailbox and refresh
the Orchestrator's Agentverse registration so its Payment Protocol is advertised. The same
address-targeted `/connect` endpoint used for the existing Bureau agents works for both; no separate
host or public inbound endpoint is needed. Preserve all six existing seeds.

## User flow

1. Complete the existing procurement, approvals, and acknowledgment/exception flow.
2. The final completion card includes **Pay confirmed total** when payments are enabled and the
   total is positive and not already collected. The card still includes Done.
3. Clicking Pay sends `request_payment` with the workflow UUID. The Orchestrator verifies the
   original conversation and completed state, then calculates the amount from the saved confirmed
   commitments, including accepted counterproposals.
4. The Payment Agent creates or reuses the workflow's Stripe Checkout session. The Orchestrator
   sends `RequestPayment` for protocol-aware buyers, followed by a native ASI:One chat message:
   a fenced `json` block with `type: "stripe_payment"`, `client_secret`, `publishable_key`,
   `checkout_session_id`, integer `amount` in minor units, `currency`, `description`, and `action`.
   Both messages refer to the same embedded session; no hosted checkout is created.
5. When the UI sends `CommitPayment`, the Orchestrator checks the sender, recipient, reference,
   session ID, amount, currency, and payment method. The Payment Agent verifies paid status,
   amount, currency, and workflow metadata directly with Stripe.
   Native ASI:One chat instead sends `{"stripe_checkout_session_id":"cs_..."}`, optionally
   preceded by an agent mention. That callback is matched to the original conversation's saved
   session and uses the same Stripe verification and durable receipt path. The callback is a
   request to verify, never evidence of payment itself. Unpaid sessions remain pending.
6. The receipt and `PAYMENT_COLLECTED` audit event commit together in PostgreSQL before the buyer
   receives `CompletePayment`. The message confirms merchant collection, not supplier payouts.

`show_completion` is read-only: it does not create checkout or request payment. After payment,
redisplay that summary to see **Payment collected: Yes** and a Done button instead of Pay.
Declining a prompt does not cancel supplier orders, delete pending records, or expire Stripe
checkout. Pay can be used again to resume the same checkout or reconcile a paid session whose UI
confirmation was lost. A declined prompt contains no workflow reference, so it never changes the
state of an ambiguously selected workflow.

## Persistence and retry behavior

- Checkout creation uses a stable Stripe idempotency key and the existing SQL side-effect journal.
  API retry parameters remain identical; expiry is read from Stripe's session response.
  The Stripe key hashes the pinned API version and complete request parameters, so the corrected
  format cannot collide with the earlier rejected requests. The local journal lookup remains
  stable, and already saved sessions are reused before any new creation request. Errors do not
  trigger random key rotation or automatic creation of a replacement session.
- Pending sessions and receipts are stored per workflow in `workflow_runs.state.payment`, rather
  than container-local agent storage. Multiple workflows in one conversation do not overwrite one
  another. No new schema migration is required beyond the existing `0007` schema.
- Receipt/audit writes are transactional. If saving fails, no CompletePayment is sent; confirmation
  can be retried without paying again. Duplicate successful confirmations acknowledge the saved
  receipt without making another Stripe request or adding another receipt event.
- Stripe uses finite retention for idempotency keys. This integration does not provide an unlimited
  recovery guarantee for a session creation whose response and journal success were both lost.
- Expired unpaid checkout sessions require operator review before replacement. They are not
  automatically recreated. There is no background Stripe webhook receiver or reconciliation
  worker; recovery of a paid pending session is triggered when the user resumes Pay.

## Verification

The automated tests use actual protocol handlers, HTTPX's mocked Stripe transport, and file-backed
SQL storage. They cover session reuse after restart, creation retry after Stripe acceptance,
multiple workflows per chat, confirmation replay, currency/amount/workflow mismatches, sender and
recipient checks, atomic receipt/audit rollback, declined prompts, expired sessions, and optional
agent construction. The existing procurement handler test verifies that completion and summary
redisplay make no checkout call, and only the emitted Pay selection invokes payment.
The request contract fixture in `tests/fixtures/stripe_checkout_contract.json` records selected
constraints from Stripe's published OpenAPI schema, its source commit, and API version. The test
server rejects unknown request fields, deprecated UI enums, missing version headers, and changed
parameters under the same key. Tests also cover reuse of saved legacy sessions and safe HTTP
error diagnostics without exposing response bodies.

An isolated PostgreSQL database was also used to verify migrations, JSON session lookup,
conversation isolation, transactional rollback, and durable idempotent receipts.

On 2026-10-18, the corrected client also created and retrieved a real Stripe test-mode Checkout
Session for the existing completed workflow's USD 117,810.00 total; both requests returned HTTP
200. The session was saved in the durable journal for reuse. No payment was submitted and no
purchase order was modified. Embedded checkout rendering, redirects, and payment callback
sender/session behavior still need a live client run after rebuilding the agent host.

On the same date, ASI:One's actual public chat bundle was inspected and its Stripe parser (module
2157) executed locally in Node against the generated chat message. It accepted the payload and
received the expected amount, currency, and session ID. Handler tests cover its native callback,
duplicate confirmations, unpaid sessions, incorrect amounts/currencies/workflows, another sender,
and unknown sessions. This checks the production parser contract, not a completed browser payment.

## References checked

- [Stripe Checkout Session creation](https://docs.stripe.com/api/checkout/sessions/create)
- [Stripe API versioning](https://docs.stripe.com/api/versioning)
- [Stripe request idempotency](https://docs.stripe.com/api/idempotent_requests)
- [Fetch Stripe Horoscope tutorial](https://innovationlab.fetch.ai/resources/docs/examples/agent-transaction/stripe-horoscope-payment-protocol)
- [Fetch payment agent examples](https://github.com/fetchai/innovation-lab-examples/tree/main/stripe-payment-agents)
- [Hackathon resource document](https://docs.google.com/document/d/10w0c9L1GL8FkeciTGyBzUCWUgrEsKJgZh4hxG_96XLs)
- [ASI:One public chat bundle inspected on 2026-10-18](https://asi1.ai/_next/static/chunks/13285-07cdfc69eb3689ed.js)

The hackathon document links the Fetch examples; it does not define a separate Stripe API
contract. The example source still uses older Stripe parameters, so the pinned Stripe API schema
governs the HTTP request while the examples inform the Payment Protocol metadata.
The tutorial's inspector guidance is insufficient for the current direct-chat path: ASI:One's
public frontend also contains a native `stripe_payment` renderer with a chat-message callback.
