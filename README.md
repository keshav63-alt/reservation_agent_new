# Restaurant Reservation Voice Agent

A real-time voice agent that answers calls for a restaurant and handles table reservations end to end. Callers speak naturally in the browser; the agent checks availability, books, looks up, reschedules and cancels reservations, and logs every call to Supabase.

The agent introduces itself as **Rosi**, the receptionist for *Demo Bistro*.

## How it works

```
Browser mic ──► Sarvam STT ──► Groq LLM (+ reservation tools) ──► Sarvam TTS ──► Browser audio
   (WebRTC)    (en-IN, realtime)   openai/gpt-oss-20b              bulbul:v3        (WebRTC)
                                         │
                                         ▼
                              services/reservations.py ──► Supabase (Postgres)
```

- **Pipeline:** [Pipecat](https://github.com/pipecat-ai/pipecat) over a WebRTC transport.
- **STT / TTS:** Sarvam (realtime STT, `bulbul:v3` voice `shubh`, English-India).
- **LLM:** Groq `openai/gpt-oss-20b` with low reasoning effort and a 300-token cap to keep replies short and fast.
- **Turn-taking:** external turn start/stop strategies driven by the STT transcript (0.5s silence, 1.2s fallback).
- **Data:** Supabase (Postgres) via the synchronous client, always called through `asyncio.to_thread` so audio is never blocked.

## Features

- Check availability for a date, time and party size, with up to three nearby alternatives when a slot is full
- Book a table (smallest suitable table is chosen automatically) and get a 6-digit reservation ID that is easy to say aloud
- Look up a caller's upcoming reservations by phone number
- Reschedule a reservation, optionally changing the party size
- Cancel a reservation, with an optional reason
- Ownership check: cancel and reschedule require the reservation ID **and** the matching phone number
- Prompt guardrails: explicit confirmation before any write, no invented phone numbers, availability or reservation IDs, no bulk cancellation from a single "cancel all"
- Per-call session metrics (LLM tokens, TTS characters, STT seconds, TTFB latency) printed when the caller disconnects
- Call records and transcripts saved to Supabase

## Project structure

```
.
├── bot/
│   ├── bot.py            # Pipecat pipeline, services, metrics, call-record saving
│   ├── tools.py          # LLM-callable tools: argument cleanup, ownership check
│   └── prompts.py        # System prompt for Rosi
├── services/
│   ├── reservations.py   # Booking logic and business rules
│   ├── call_log.py       # Non-blocking call/event logger (queue + batched writes)
│   └── call_logging.py   # Identical copy of call_log.py (see Known issues)
├── db/
│   ├── connection.py     # Shared Supabase client
│   ├── seed.py           # Demo restaurant + tables
│   └── database.md       # Detailed notes on the data layer
├── tests/
│   ├── test_reservations.py   # pytest suite (runs against real Supabase)
│   └── test_tools.py          # Manual smoke script for the tools
└── pyproject.toml
```

## Requirements

- Python 3.14 (`requires-python = ">=3.14,<3.15"`)
- [uv](https://docs.astral.sh/uv/)
- A [Supabase](https://supabase.com) project
- A [Sarvam](https://www.sarvam.ai) API key (STT and TTS)
- A [Groq](https://groq.com) API key (LLM)

## Setup

### 1. Install dependencies

```bash
uv sync
```

### 2. Environment variables

Create a `.env` file in the project root:

```
SARVAM_API_KEY=your-sarvam-key
GROQ_API_KEY=your-groq-key
SUPABASE_DATA_URL=https://your-project-ref.supabase.co
SUPABASE_SERVICE_KEY=your-service-role-key
```

- `SUPABASE_DATA_URL` must be the bare project URL, with no path and no trailing slash.
- `SUPABASE_SERVICE_KEY` must be the **service_role** key, not the anon/publishable key.
- Keep `.env` out of version control and never print the service key.

### 3. Create the tables

Run the schema in the Supabase SQL Editor. The agent uses five tables:

| Table | Purpose |
|---|---|
| `restaurants` | Opening/closing time, `slot_duration` (minutes), name, timezone |
| `tables` | Physical tables per restaurant, each with a `capacity` |
| `reservations` | Bookings: customer, date, time, party size, `status`, `slot_key` |
| `calls` | One row per call: start/end time, status, phone, summary, transcript, outcomes |
| `call_events` | Ordered events within a call (`seq`, `kind`, `content`, `data`) |

`reservations` needs a partial unique index on `slot_key` where `status = 'confirmed'`. This stops two confirmed bookings taking the same table slot while still allowing a cancelled slot to be rebooked. `slot_key` is `{table_id}_{date}_{time}`. See [`db/database.md`](db/database.md) for more on the schema and rules.

### 4. Seed demo data (optional)

```bash
uv run python -m db.seed
```

Creates restaurant `r1` (Demo Bistro, 12:00 to 23:00, 90-minute slots) with tables `t1` and `t2` (2 seats), `t3` (4 seats) and `t4` (6 seats). The seed uses upserts, so running it again is safe.

## Running the agent

From the project root:

```bash
uv run python -m bot.bot
```

Then open the Pipecat prebuilt client in your browser (by default `http://localhost:7860/client`), allow microphone access and start talking.

## Business rules

- Bookings start on the hour or half hour.
- A booking must start at or after opening and finish (start + `slot_duration`) by closing time. With 12:00 to 23:00 and 90-minute slots, the last start is 21:30.
- A booking blocks its table for the full slot. Back-to-back bookings are allowed.
- The smallest free table that fits the party is chosen.
- Past dates and times are rejected (timezone `Asia/Kolkata`).
- Party size must be a whole number of at least 1; name and phone must not be blank.
- If two callers take the same slot at the same moment, the unique index rejects one and that caller is offered alternatives.
- 10-digit phone numbers are normalised to `+91XXXXXXXXXX`.

## Tools exposed to the LLM

| Tool | Purpose |
|---|---|
| `check_availability(date, time, party_size)` | Is a table free? Returns alternatives if not |
| `book_reservation(customer_name, customer_phone, date, time, party_size, special_requests?)` | Create a reservation after caller confirmation |
| `find_my_reservations(customer_phone)` | List upcoming confirmed reservations |
| `reschedule_reservation(reservation_id, customer_phone, new_date, new_time, new_party_size?)` | Move a booking |
| `cancel_reservation(reservation_id, customer_phone, reason?)` | Cancel a booking |

Every tool returns a dict with `ok` and `message` so the model can phrase an accurate reply. Tool arguments are cleaned up first (for example `"2"` becomes `2`).

## Testing

```bash
uv run pytest -v
```

- `tests/test_reservations.py` runs against your **real Supabase project**, so it needs network access and a valid `.env`. It creates an isolated restaurant (`test_r`) with two tables and removes it afterwards; your real data is not touched.
- Run a subset with `-k`, for example `uv run pytest -v -k reschedule`.
- `tests/test_tools.py` is a manual smoke script that calls the tools with a fake params object. Run it with `uv run python -m tests.test_tools`; it writes to the database (it books a table on 2030-01-15).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `PGRST125: Invalid path specified in request URL` | `SUPABASE_DATA_URL` has a path or trailing slash. Use only `https://<ref>.supabase.co`. |
| `new row violates row-level security policy` (`42501`) | You are using the anon key. Use the `service_role` key. |
| `RuntimeError: Missing SUPABASE_DATA_URL or SUPABASE_SERVICE_KEY` | Check the variable names in `.env`. |
| `KeyError: 'SARVAM_API_KEY'` or `'GROQ_API_KEY'` | Add the missing key to `.env`. |
| `Restaurant 'r1' was not found in the database` | Run the seed script. |
| `DeprecationWarning: 'timeout' / 'verify' parameter is deprecated` | Comes from inside `supabase-py`; safe to ignore. |

## Security notes

- The service key bypasses Row Level Security. Use it only on the backend.
- Keep Row Level Security **enabled** on all tables so the public anon key cannot read reservations or phone numbers.
- Rotate the key in Supabase if it is ever exposed.

## Known issues and limitations

- `RESTAURANT_ID = "r1"` is hardcoded in `bot/bot.py` and `bot/tools.py`, and the prompt names "Demo Bistro", so the agent serves one restaurant.
- The caller's phone number is collected by voice and cannot be verified, so ownership checks rely on the caller knowing both the reservation ID and phone number.
- Two callers booking *different but overlapping* start times at the same instant could both succeed. A Postgres range-exclusion constraint would remove this.
- The timezone is a constant in `services/reservations.py`; moving it to the `restaurants` table would allow multiple timezones.
- `services/call_logging.py` duplicates `services/call_log.py`; only `call_log` is imported.
- `bot/bot.py` saves the transcript itself at the end of the call, while `CallLogger` in `services/call_log.py` (used by the tools) is not started in the pipeline yet.

## Roadmap

- Wire `CallLogger` into the pipeline for live per-event logging
- Multi-restaurant support (resolve the restaurant per call)
- Telephony transport (for example Twilio) in addition to WebRTC
- Filler speech while tools run (currently commented out in `bot.py`)