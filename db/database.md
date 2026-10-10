# Database Layer: Restaurant Reservation Agent

The data and business-logic layer for the restaurant reservation voice agent. It stores bookings in Supabase (Postgres) and exposes plain Python functions for checking availability, booking, cancelling, rescheduling and logging calls. The voice pipeline (Pipecat + Groq + Sarvam) calls these functions as LLM tools, so this layer has no dependency on voice or AI and can be tested on its own.

## Folder layout

```
db/
├── connection.py          # creates the shared Supabase client
├── reservations.py        # all booking logic (the functions the agent calls)
├── test_reservations.py   # pytest suite (runs against real Supabase)
├── seed.py                # one-time demo data (restaurant r1 + 4 tables)
└── README.md
```

## Requirements

- Python 3.10+
- A Supabase project with the four tables created (see [Schema](#schema))
- Packages: `supabase`, `python-dotenv`, and `pytest` (dev)

```bash
uv add supabase python-dotenv
uv add --dev pytest
```

## Setup

### 1. Environment variables

Create a `.env` file in the project root:

```
SUPABASE_DATA_URL=https://your-project-ref.supabase.co
SUPABASE_SERVICE_KEY=your-service-role-key
```

- `SUPABASE_DATA_URL` must be the bare project URL: `https://<ref>.supabase.co`, with no path and no trailing slash.
- `SUPABASE_SERVICE_KEY` must be the **service_role** key (Supabase dashboard → Project Settings → API Keys), not the anon/publishable key.
- Add `.env` to `.gitignore`. Never commit or print the service key.

### 2. Create the tables

Run the schema below in the Supabase SQL Editor.

### 3. Seed demo data (optional, run once)

```bash
cd db
uv run seed.py
```

This creates restaurant `r1` with tables `t1` to `t4`. Running it twice fails on the duplicate `r1`.

### 4. Check the connection

```bash
uv run connection.py
```

It should print `[]` or your restaurant rows.

## Schema

| Table | Purpose |
|---|---|
| `restaurants` | One row per restaurant: opening/closing time, `slot_duration` (minutes) |
| `tables` | Physical tables per restaurant, each with a `capacity` |
| `reservations` | Bookings: customer, date, time, party size, `status`, `slot_key` |
| `calls` | One row per phone call: times, status, summary, tokens used |

Key points:

- `reservations.status` is one of `confirmed`, `cancelled`, `completed`, `no_show`.
- A **partial unique index** on `reservations(slot_key) WHERE status = 'confirmed'` stops two confirmed bookings from taking the same table slot, while still allowing a cancelled slot to be rebooked.
- `slot_key` is built as `{table_id}_{date}_{time}`.

## Public functions (`reservations.py`)

Every function returns a plain dict with an `ok` flag and a `message`, so the LLM can read the result and phrase a reply. Dates are `YYYY-MM-DD` and times are `HH:MM` (24-hour).

| Function | What it does |
|---|---|
| `check_availability(restaurant_id, date, time, party_size)` | Is a table free? Returns `available` and, when full, nearby `alternatives`. |
| `book_reservation(restaurant_id, name, phone, date, time, party_size, email=None, special_requests=None)` | Picks the smallest suitable table and books it. Returns a 6-digit `reservation_id`. |
| `cancel_reservation(reservation_id, reason=None)` | Marks a confirmed reservation as cancelled and frees the slot. |
| `reschedule_reservation(reservation_id, new_date, new_time, new_party_size=None)` | Moves a booking, optionally changing party size. The original is left unchanged on failure. |
| `get_reservation(reservation_id)` | Returns the reservation row, or `None`. |
| `find_reservations_by_phone(phone)` | Returns the caller's confirmed reservations. |
| `get_restaurant(restaurant_id)` | Returns the restaurant row, or `None`. |
| `log_call(phone_no, start_time, end_time, status, restaurant_id, appointment_details=None, summary=None, tokens_used=None)` | Saves a call record. |

Example:

```python
from services.reservations import check_availability, book_reservation

check_availability("r1", "2026-12-20", "20:00", 2)
# {'ok': True, 'available': True, 'message': 'A table is available.'}

book_reservation("r1", "Asha", "+919999999999", "2026-12-20", "20:00", 2)
# {'ok': True, 'reservation_id': '482913', 'table': 'T1', ...}
```

## Business rules

- **Time steps:** bookings start on the hour or half hour (`STEP_MIN = 30`).
- **Opening hours:** a booking must start at or after opening and *finish* (start + `slot_duration`) by closing time. With 12:00 to 23:00 and 90-minute slots, the last start is 21:30.
- **Overlap logic:** a booking blocks its table for the full `slot_duration`. Back-to-back bookings are allowed (21:30 can follow a 20:00 booking that ends at 21:30).
- **Table choice:** the smallest free table that fits the party is chosen, keeping larger tables free for larger parties.
- **Alternatives:** when a time is full, up to three nearby free times are returned.
- **Past dates** are rejected (restaurant timezone is `TZ = "Asia/Kolkata"` in `reservations.py`).
- **Input validation:** party size must be a whole number of at least 1; customer name and phone must be non-blank; dates and times must be valid and correctly formatted.
- **Race conditions:** if two callers grab the same slot at the same moment, the unique index rejects one, and that caller gets "slot just taken" with alternatives.
- **Reservation IDs** are 6 random digits, easy for a caller to say back.

## Running the tests

```bash
cd db
uv run pytest -v
```

- The suite runs against your **real Supabase project**, so it needs network access and the correct `.env`.
- It creates an isolated restaurant (`test_r`) with two tables and deletes it afterwards. Your real data (`r1`) is not touched. If a run crashes, the next run cleans up any leftovers.
- Run a subset with `-k`, for example `uv run pytest -v -k reschedule`.
- To see the full result dict when a test fails, the key assertions use `assert r["ok"], r`.

What's covered: availability rules, booking and table choice, overlap and back-to-back slots, cancellation and rebooking, rescheduling (including moving onto its own old slot), lookup by phone, call logging, input validation (party size, name, phone, time, date) and the database unique index.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `PGRST125: Invalid path specified in request URL` | `SUPABASE_DATA_URL` has a path, trailing slash or is a dashboard link. Use only `https://<ref>.supabase.co`. |
| `new row violates row-level security policy` (code `42501`) | The client is using the anon/publishable key. Use the `service_role` (or `sb_secret_...`) key. |
| `Invalid API key` | Wrong or truncated key, or a stray quote or space in `.env`. |
| `Restaurant not found.` | The restaurant ID doesn't exist. Run `seed.py`, or check the ID. |
| `collected 0 items` | Pytest only collects files named `test_*.py`. Check the test file name. |
| `KeyError: 'SUPABASE_URL'` | Variable names differ between files. This project uses `SUPABASE_DATA_URL` and `SUPABASE_SERVICE_KEY`. |
| `VIRTUAL_ENV ... does not match the project environment` | Another virtualenv is active in your shell. Run `deactivate`. `uv run` still uses the project `.venv`, so it's harmless. |
| `DeprecationWarning: 'timeout' / 'verify' parameter is deprecated` | Comes from inside `supabase-py` / `postgrest-py`, not this code. Safe to ignore. |

## Security notes

- The service key bypasses Row Level Security. Use it only on the backend and keep it in `.env`.
- Never put the service key in client-side or frontend code.
- If the key is ever exposed, rotate it in Project Settings → API Keys.
- Because the agent only talks to the database from the backend, keep Row Level Security **enabled** on all tables so the public anon key cannot read or write reservations or customer phone numbers.

## Known limitations

- Two callers booking *different but overlapping* start times at the exact same instant could both succeed. This is rare, and a Postgres-side lock or range-exclusion constraint would remove it.
- The restaurant timezone is a constant in `reservations.py`. Move it to the `restaurants` table for multi-timezone support.
- Repository calls use the synchronous Supabase client. Inside the async voice pipeline, call them with `asyncio.to_thread(...)` so audio is never blocked.

## Next steps

1. Tool schemas for the LLM (`check_availability`, `book_reservation`, `cancel_reservation`, `reschedule_reservation`, `find_reservations_by_phone`).
2. Tool handlers that convert LLM arguments (for example `"2"` to `2`) and call these functions.
3. The Pipecat pipeline: transport, VAD, Sarvam STT, Groq LLM, Sarvam TTS.