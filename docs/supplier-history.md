# Supplier order history

The supplier backend saves new purchase orders (850) and change orders (860) in one
`supplier_order_history` PostgreSQL table. There is no backfill. Original orders and changes
remain separate records, related by manufacturer ID, supplier ID and PO number. Their totals
must not be added together as if each change were another purchase.

Each record includes manufacturer identity, supplier, PO number/version, UTC timestamp, requested
lines, currency/total, the original X12 submission, acceptance response and acknowledgment.
Acceptance and acknowledgment commit together before returning HTTP 202. Repeated submissions
reuse their saved transaction and acknowledgment, including after restart or a demo reset.
Reusing a key with different terms or submitting the same PO version under another key is refused.
Rejected changes remain visible as `CHANGE_REJECTED`; `ACKNOWLEDGED` is a supplier transaction
status, not confirmation of physical delivery. RFQ and scenario settings remain in memory.

## Manufacturer identity

Configure the company-side agent host in the private `.env`, for example:

```dotenv
MANUFACTURER_ID=urbanx
MANUFACTURER_NAME=UrbanX Motors
```

Defaults are `manufacturer-demo` / `Demo Manufacturer`. The Procurement Agent gateway passes
these values with PO and change submissions. Direct backend callers can include
`manufacturer_id` and `manufacturer_name` in the submission body. This does not change agent
seeds, addresses, approval payloads or purchase-order X12 generation.

## ASI:One usage

Ask the public Supplier Agent:

```text
@sm-supplier show purchase order history
@sm-supplier show history for manufacturer urbanx supplier-alpha
```

The card shows up to ten records with manufacturer, supplier, PO/version, date, acknowledgment
status, requested items and total. **Show more** retains filters. **Filter history** opens a form
with optional manufacturer and supplier filters. **Done** has no workflow side effects. Cards
show at most twenty lines per record; complete lines are retained in the database/API response.

History summaries are deliberately readable through the Supplier Agent's public chat, as a shared
demo dataset. Manufacturer labels are not tenant authorization. API keys and raw X12/transaction
responses are not included in history cards or the history endpoint. Public chat still cannot
place orders, changes, or cancellations. Authenticated status endpoints return the original
acknowledgments unless the order has been cancelled, in which case they report `CANCELLED`.
Confirmed cancellations retain original submissions and acknowledgments and persist a cancellation
receipt. Accepted changes to the cancelled PO are also marked cancelled. See
[replacement sourcing](replacement-sourcing.md) for the approved cancellation flow and endpoint.

The authenticated read-only HTTP endpoint is:

```text
GET /api/v1/order-history?manufacturer_id=urbanx&supplier_id=supplier-alpha&offset=0
X-API-Key: <supplier API key>
```

Both filters are optional. Responses contain `items` and `next_offset` (null on the last page).

## Deploy the change

Preserve existing seeds, database volumes and orders. Build the two services, apply migration
`0009` using the new image, then recreate only the supplier simulator and agent host:

```bash
docker compose build agent-host supplier-simulator
docker compose run --rm --no-deps agent-host alembic upgrade head
docker compose up -d --no-deps agent-host supplier-simulator
```

No existing orders are imported. The history starts with submissions received after this update.
Demo scenario/reset endpoints preserve history; no history-deletion API is added.

## Validation

Tests exercise real FastAPI routes and the Supplier Agent chat handler: durable restart/reset
recovery, idempotent replay, changed-term rejection, manufacturer/supplier filtering, pagination,
accepted/rejected changes, atomic database failure and read-only cards. Existing backend tests
use explicit isolated SQLite databases instead of requiring the running demo database.

Migration `0008` was also exercised in a disposable PostgreSQL database, including downgrade and
re-upgrade, schema parity, concurrent identical submissions, restart/reset recovery and supplier
isolation. This does not establish live rendering of the new history cards in ASI:One.
