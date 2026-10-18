# Cancel a supplier PO and find replacement supply

At acknowledgment exception review, choose **Cancel and find replacement**. A separate review
asks for approval to cancel the entire affected supplier PO, including its changes. Other supplier
POs stay active. **Stop workflow** remains a separate local stop; it does not cancel active supplier
orders.

The Procurement Agent sends the approved cancellation through the Supplier Agent to the mock
backend. Cancellation is persisted with a stable receipt. No replacement PO can be submitted until
that receipt is confirmed. A failed cancellation shows **Retry cancellation**; a lost reply is
retried automatically using the same request and receipt. Tentative inventory reservations stay
reserved throughout replacement planning.

After confirmation, the Orchestrator requests fresh quotes for all quantities on the cancelled PO.
Suppliers cancelled during this workflow are excluded. Other eligible suppliers are queried,
including suppliers already fulfilling unaffected orders. At least one usable quote is required;
multiple alternative suppliers may not stock every part. Plans must meet the manufacturing
deadline and fit the remaining budget after retained orders. Supplier minimum quantities and pack
sizes still apply, so ordered quantities can exceed the cancelled quantities.

Selecting a plan opens its PO review directly. **Approve and send** is required before supplier
submission; there is no intermediate plan approval card. Replacement POs have unique numbers
such as `SM-<workflow>-R01-01`. Only the new batch is sent. Its acknowledgment follows the normal
comparison, exception, acceptance, and counterproposal paths. Another cancellation starts another
replacement round. Cancelled POs remain in history but are excluded from final commitments and
the payable total.

The cancellation turn sends one reply containing the plans or retry card. It does not send an
earlier plain-text progress reply that can end the pending ASI:One turn before the card arrives.
If a plans card was missed, send `{"action":"show_plans"}` to the Orchestrator in the same
conversation. This reopens stored plans while waiting for plan selection, without contacting
suppliers or sending orders.

If no alternative fits, **Retry or revise constraints** lets the user request fresh quotes with
the same constraints, or change the deadline and total workflow budget. The budget is in minor
currency units (USD cents), includes retained orders, and is not just the replacement allowance.
Blank fields retain current values. No replacement order is fabricated or automatically sent.
Stopping releases tentative reservations; retained supplier orders still need operator cancellation.

## Scope and deployment

This is a whole-PO mock-supplier REST cancellation, not a certified X12 cancellation exchange or
partial-line cancellation. It requires the original supplier transaction to exist in the persisted
supplier history. Historical orders are not backfilled. No new agent, seed, or registration is
required.

Build the updated images, apply migration `0009`, then recreate the services while preserving the
database volume and existing seeds:

```bash
docker compose build agent-host supplier-simulator
docker compose run --rm --no-deps agent-host alembic upgrade head
docker compose up -d --no-deps agent-host supplier-simulator
```

The authenticated backend operation is:

```text
POST /api/v1/suppliers/{supplier_id}/purchase-orders/{transaction_id}/cancel
X-API-Key: <supplier API key>
{"manufacturer_id":"manufacturer-demo","po_number":"<PO number>","po_version":1}
```

Repeated calls return the same receipt. The manufacturer, supplier, transaction, PO number, and
original version must match. New changes to a cancelled order are rejected. Original X12 and
acknowledgments remain stored; status and history report cancellation.

## Verification

`tests/integration/test_replacement_sourcing.py` drives the actual Orchestrator and specialist
handlers, Supplier Agent tools, real supplier HTTP routes, and separate persistent databases.
It covers cancellation approval, retained orders, replacement selection and PO approval, final totals,
lost replies, supplier failure, unavailable budgets/deadlines, revised constraints, stale/foreign
decisions, repeated exceptions, stopping, durable receipts, and blocked late changes.

Live rendering of these new cards in ASI:One still requires verification after deployment.
