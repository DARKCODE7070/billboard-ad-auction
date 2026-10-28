# Bidboard: billboard ad auction platform

A web marketplace where billboard owners list ad slots, advertisers bid, and the winner is picked automatically when the deadline passes. Nothing here streams to physical screens: the platform runs the auction and the paperwork (winner, price, creative approval) and stops at the hand-off.

Stack: Python 3.11+, FastAPI, SQLAlchemy 2, PostgreSQL (SQLite for quick local runs), Celery + Redis, and a plain HTML/CSS/JS frontend with no build step.

## Run it

### Quickest: no Docker, no Redis (SQLite)

```bash
pip install -r requirements.txt
python -m app.seed --reset          # demo users, 10 billboards, 10 live auctions
python -m uvicorn app.main:app --reload
# open http://localhost:8000
```

An in-process sweeper thread closes auctions at their deadline, so the demo works without Celery.

Demo accounts (password `password123`): `chaipoint@demo.test`, `novamotors@demo.test`, `lumen@demo.test` (advertisers) and `meridian@demo.test`, `skyline@demo.test` (owners). The seed schedules one auction to close in about 3 minutes so you can watch the whole cycle.

### Full stack: PostgreSQL + Redis + Celery worker + beat

```bash
docker compose up --build
docker compose exec web python -m app.seed
# open http://localhost:8000
```

## How an auction resolves

1. **List.** An owner picks a billboard, a starting bid (the floor), a minimum raise, a deadline and the dates the ad will run.
2. **Bid.** An advertiser bids manually, or ticks "auto-bid" and sets a maximum budget.
3. **Close.** At the deadline the system replays the bid ledger, picks the winner, creates a booking, and notifies the winner, the owner and everyone who lost.
4. **Hand-off.** The winner uploads a PNG, JPEG, WebP or MP4. The owner approves it or sends it back with a note.

**Pricing rule (first price).** The winner pays their own winning bid. With auto-bid, the system holds your bid just one raise above the next-highest budget, never above your maximum, so you don't pay more than you have to to stay ahead. Ties on maximum budget go to whoever bid first.

**Late bids extend the deadline.** A bid in the last 2 minutes (owner-configurable: 2, 5, or 0) pushes the deadline out by that long, so nobody wins on a last-second click. Close handling follows the new deadline.

Pricing is pluggable: `app/engine.py` has a `PRICING_RULES` registry with `first_price`. Adding second-price or lowest-offer means writing one function there and one enum value in the API schema. The ledger and settlement flow don't change.

## Design notes

| Concern | How it's handled |
|---|---|
| Engine | `app/engine.py` is pure Python: no DB, no clock. Bids are validated and the standing is computed from a position per bidder. |
| Source of truth | `bids` is an append-only ledger. Settlement replays it through the same engine, so the stored price columns are a cache and the result is auditable. |
| Race conditions | `place_bid` and `close_auction` take `SELECT ... FOR UPDATE` on the auction row, so one auction's bids are strictly ordered. `UNIQUE(auction_id, seq)` is the backstop, and the service retries on conflict. |
| Exactly-once close | `close_auction` locks the row and checks `status == 'open'`, so duplicate Celery deliveries or two workers do nothing the second time. A booking has a unique `auction_id`. |
| Expiry | Two layers: a Celery ETA task queued at the exact deadline (re-queues itself if the deadline moved), and a beat sweeper every 10 s that catches anything missed. |
| Privacy | Bid history shows "Bidder 1, 2, ..." and never exposes maximum budgets. Creatives are served only to the two parties on a booking. |
| Money | `NUMERIC(12,2)` and `Decimal` throughout. Display is INR with Indian digit grouping. |

## Project layout

```
app/
  engine.py       pure auction rules (bids, proxy, settlement)
  services.py     transactions: create, bid, close, upload, review
  api.py          FastAPI routes and serialisers
  tasks.py        Celery tasks and beat schedule
  scheduling.py   ETA-task hook (no-op without Celery)
  models.py       SQLAlchemy tables
  security.py     password hashing and signed tokens
  seed.py         demo data
  static/         the frontend (index.html, css/, js/)
tests/            engine, service and HTTP tests
schema.sql        PostgreSQL DDL (regenerate with export_schema.py)
```

## API

Interactive docs at `/docs`. Main routes, all under `/api`:

| Route | Who |
|---|---|
| `POST /auth/register`, `POST /auth/login`, `GET /me` | anyone |
| `GET /auctions` (filters: `q city format min_price max_price ending_within_hours sort status`), `GET /auctions/{id}`, `GET /auctions/{id}/bids`, `GET /facets`, `GET /stats` | anyone |
| `POST /auctions/{id}/bids` `{amount, max_amount?}` | advertiser |
| `POST /billboards`, `POST /auctions`, `POST /auctions/{id}/cancel`, `GET /owner/billboards`, `GET /owner/auctions` | owner |
| `POST/DELETE /auctions/{id}/hide`, `GET /me/hidden` (personal hide list) | advertiser |
| `GET /me/bids`, `GET /me/placements`, `POST /placements/{id}/creative` | advertiser |
| `POST /placements/{id}/payment` `{paid, ref?}` | owner (records a manual payment) |
| `POST /placements/{id}/review` | owner (approval requires the booking to be marked paid) |
| `GET /notifications`, `POST /notifications/read` | signed in |

Errors have one shape: `{"code": "BID_TOO_LOW", "message": "...", "details": {...}}`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

67 tests run by default and cover the engine rules, 40 randomised bidding sequences (invariants: leader has the highest budget, price never falls, ledger replays to the same result), closing, notifications, the upload and review cycle, and a full HTTP journey. A 68th test hammers a single auction from 12 threads; it needs a real PostgreSQL because SQLite has no row locks:

```bash
TEST_DATABASE_URL=postgresql+psycopg2://bidboard:bidboard@localhost:5432/bidboard_test pytest tests/test_flow.py -k concurrent
```

## Known limits

- **PostgreSQL locking and the Celery path were not exercised in the environment this was built in** (no Postgres server or Redis there). The SQL is standard and the locking uses SQLAlchemy's `with_for_update`, but run the concurrency test above and watch a worker close an auction before relying on it.
- Notifications are in-app only. "Email" is a log line in `services.notify`; plug in your mail provider there.
- Payments are tracked manually: the owner clicks "Mark as paid" (optional UPI/UTR reference) after checking their bank. There is no payment gateway, and the creative cannot be approved until the booking is marked paid.
- Auth is a small HMAC-signed token with scrypt password hashing. Fine for a prototype; use a vetted JWT/OIDC library and set a real `SECRET_KEY` before deploying.
- The deadline can extend repeatedly while people keep bidding in the final window. There's no cap on extensions.
- Live updates use 3-second polling, not WebSockets.
- Tables are created on startup. Use Alembic for schema changes once you have real data.
- Uploaded creatives live on local disk (`UPLOAD_DIR`). Use object storage if you run more than one web instance.
