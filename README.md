# Restaurant Reservation Voice Agent

A real-time AI voice receptionist for restaurants. Callers talk to it in the browser, and it checks availability, books tables, looks up, reschedules and cancels reservations, and saves every call to a database, all in natural spoken conversation.

The agent introduces itself as **Rosi**, the receptionist for *Demo Bistro*.

---

## Table of contents

- [The problem it solves](#the-problem-it-solves)
- [Where it can be used](#where-it-can-be-used)
- [Features](#features)
- [How it works](#how-it-works)
- [Tech stack](#tech-stack)
- [Project structure](#project-structure)
- [Getting started](#getting-started)
- [Running the agent](#running-the-agent)
- [Example conversation](#example-conversation)
- [Tools exposed to the LLM](#tools-exposed-to-the-llm)
- [Business rules](#business-rules)
- [Database schema](#database-schema)
- [Safety and guardrails](#safety-and-guardrails)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Security notes](#security-notes)
- [Known limitations](#known-limitations)
- [Roadmap](#roadmap)

---

## The problem it solves

Taking reservations by phone sounds simple, but it is a real operational headache for restaurants:

- **Calls arrive at the worst time.** Most reservation calls come during lunch and dinner service, exactly when staff are busiest serving guests. Calls get missed or rushed.
- **Missed calls are lost bookings.** A guest who can't get through usually books somewhere else.
- **Manual booking is error-prone.** Paper diaries and shared spreadsheets lead to double bookings, wrong table sizes and forgotten changes.
- **Staff time is wasted on repetitive work.** Answering "do you have a table for four tomorrow at 8?" dozens of times a day takes time away from guests.
- **Small restaurants can't afford a dedicated receptionist.** Hiring someone to sit on the phone all day is not realistic for most.

This agent answers instantly, handles the whole booking conversation by voice, and writes every booking straight into a database with the same rules a careful human would follow (opening hours, table sizes, no overlaps). Staff only see confirmed, conflict-free reservations.

## Where it can be used

- **Restaurants, cafes and bars** that take table reservations by phone
- **Hotel dining rooms and banquet desks**
- **Food courts and multi-outlet chains** (with the multi-restaurant extension in the [Roadmap](#roadmap))
- **Appointment-based businesses** such as clinics, salons, spas and service centres, since the same pattern of check availability, book, reschedule and cancel applies; you would change the prompt, the tool names and the schema
- **Portfolio and learning projects** for anyone building production-style voice AI with Pipecat, tool calling and Supabase

## Features

- **Natural voice conversation:** speech in, speech out, with low-latency streaming at every stage
- **Availability check:** by date, time and party size, with up to three nearby alternative times when a slot is full
- **Booking:** automatically picks the smallest suitable table and returns an easy-to-say 6-digit reservation ID
- **Lookup:** finds a caller's upcoming reservations by phone number
- **Reschedule:** moves a booking to a new date or time, optionally changing the party size
- **Cancel:** cancels a booking with an optional reason, freeing the table
- **Ownership check:** cancel and reschedule need the reservation ID **and** the matching phone number
- **Human-like filler speech:** short phrases such as "Let me check that for you" play while a tool runs, so the caller never hears dead air
- **Guardrails in the prompt:** explicit confirmation before any change, no invented phone numbers, availability or IDs
- **Call records:** transcript, caller phone, start and end time and booking outcomes saved to Supabase
- **Session metrics:** LLM tokens, TTS characters, STT audio seconds and time-to-first-byte latency printed after each call

## How it works

```
Browser mic ──► Sarvam STT ──► Groq LLM (+ reservation tools) ──► Sarvam TTS ──► Browser audio
   (WebRTC)    (en-IN, realtime)    openai/gpt-oss-20b             bulbul:v3        (WebRTC)
                                           │
                                           ▼
                                  bot/tools.py  (validate, clean, check owner)
                                           │
                                           ▼
                                  services/reservations.py  (business rules)
                                           │
                                           ▼
                                    Supabase (Postgres)
```

1. The caller's voice is streamed from the browser over WebRTC.
2. **Sarvam STT** turns it into text; turn-taking is decided by short silence detection on the transcript.
3. The **Groq LLM** understands the request and decides whether to call a tool.
4. When a tool is called, a **filler phrase** is spoken immediately while the tool runs.
5. The tool cleans the arguments, checks ownership where needed, then calls the business logic in `services/reservations.py`, which reads and writes Supabase.
6. The tool result goes back to the LLM, which phrases the answer, and **Sarvam TTS** speaks it.
7. When the caller disconnects, metrics are printed and the full transcript is saved to the `calls` table.

## Tech stack

| Layer | Technology |
|---|---|
| Voice pipeline | [Pipecat](https://github.com/pipecat-ai/pipecat) with a WebRTC transport |
| Speech-to-text | Sarvam realtime STT (`en-IN`) |
| LLM | Groq `openai/gpt-oss-20b` (low reasoning effort, 300 max tokens) |
| Text-to-speech | Sarvam `bulbul:v3`, voice `shubh` |
| Database | Supabase (Postgres) |
| Backend language | Python 3.14 |
| Package manager | [uv](https://docs.astral.sh/uv/) |
| Testing | pytest |
| Logging | loguru |

## Project structure

```
.
├── bot/
│   ├── bot.py              # Pipeline wiring only: services, aggregators, event handlers
│   ├── config.py           # Shared settings (restaurant ID, default country code)
│   ├── prompts.py          # System prompt for Rosi
│   ├── tools.py            # LLM-callable tools + argument cleanup + ownership check
│   ├── filler.py           # Filler phrases spoken while tools run
│   ├── metrics.py          # SessionMetricsTracker (tokens, characters, latency)
│   └── restaurant_info.py  # Builds restaurant facts + date calendar for the prompt
├── services/
│   ├── reservations.py     # Booking logic and business rules
│   ├── call_record.py      # Builds the transcript and saves it to the calls table
│   └── call_log.py         # Optional non-blocking per-event logger (see Known limitations)
├── db/
│   ├── connection.py       # Shared Supabase client
│   ├── seed.py             # Demo restaurant and tables
│   └── database.md         # Notes on the data layer
├── tests/
│   ├── test_reservations.py  # pytest suite (runs against real Supabase)
│   └── test_tools.py         # Manual smoke script (not a pytest test)
└── pyproject.toml
```

The design keeps each concern in one place: `bot.py` only wires the pipeline, `tools.py` talks to the LLM, `services/` holds the business logic, and `db/` holds the connection and seed data. The data layer has no dependency on voice or AI, so it can be tested on its own.

## Getting started

### Requirements

- Python 3.14
- [uv](https://docs.astral.sh/uv/)
- A [Supabase](https://supabase.com) project
- A [Sarvam](https://www.sarvam.ai) API key (STT and TTS)
- A [Groq](https://groq.com) API key (LLM)

### 1. Install dependencies

```bash
uv sync
```

### 2. Configure environment variables

Create a `.env` file in the project root:

```
SARVAM_API_KEY=your-sarvam-key
GROQ_API_KEY=your-groq-key
SUPABASE_DATA_URL=https://your-project-ref.supabase.co
SUPABASE_SERVICE_KEY=your-service-role-key
```

- `SUPABASE_DATA_URL` must be the bare project URL with no path and no trailing slash.
- `SUPABASE_SERVICE_KEY` must be the **service_role** key, not the anon or publishable key.
- Keep `.env` out of version control.

### 3. Create the tables

Run the schema described in [Database schema](#database-schema) in the Supabase SQL Editor.

### 4. Seed demo data (optional)

```bash
uv run python -m db.seed
```

This creates restaurant `r1` (Demo Bistro, 12:00 to 23:00, 90-minute slots) with tables `t1` and `t2` (2 seats each), `t3` (4 seats) and `t4` (6 seats). The script uses upserts, so running it again is safe.

### 5. Check the database connection

```bash
uv run python -m db.connection
```

It should print your restaurant rows.

## Running the agent

From the project root:

```bash
uv run python -m bot.bot
```

Open the Pipecat prebuilt client in your browser (by default `http://localhost:7860/client`), allow microphone access and start talking.

## Example conversation

> **Rosi:** Hello! I'm Rosi, your restaurant receptionist. I can help answer questions, book, cancel, or reschedule reservations. How can I help you today?
> **Caller:** Hi, I'd like a table for four tomorrow at 8 PM.
> **Rosi:** Sure, checking availability now. Good news, we have a table for four at 8 PM tomorrow. Can I take your name and phone number?
> **Caller:** Asha, 98765 43210.
> **Rosi:** Thanks, Asha. To confirm: a table for four, tomorrow at 8 PM, under Asha. Shall I book it?
> **Caller:** Yes please.
> **Rosi:** Booking that for you now. All done! Your reservation ID is 4 8 2 9 1 3.

## Tools exposed to the LLM

| Tool | Purpose |
|---|---|
| `check_availability(date, time, party_size)` | Is a table free? Returns alternative times if not |
| `book_reservation(customer_name, customer_phone, date, time, party_size, special_requests?)` | Create a reservation after the caller confirms |
| `find_my_reservations(customer_phone)` | List the caller's upcoming confirmed reservations |
| `reschedule_reservation(reservation_id, customer_phone, new_date, new_time, new_party_size?)` | Move a booking |
| `cancel_reservation(reservation_id, customer_phone, reason?)` | Cancel a booking |

Every tool returns a dict with `ok` and `message` so the model can phrase an accurate reply. Tool arguments are cleaned first: numbers sent as strings (`"2"`) are converted, and 10-digit phone numbers are normalised to `+91XXXXXXXXXX`.

## Business rules

- Bookings start on the hour or half hour.
- A booking must start at or after opening and finish (start + slot duration) by closing time. With 12:00 to 23:00 and 90-minute slots, the last start is 21:30.
- A booking blocks its table for the full slot. Back-to-back bookings are allowed.
- The smallest free table that fits the party is chosen, keeping larger tables free for larger parties.
- When a time is full, up to three nearby free times are suggested.
- Past dates and times are rejected (timezone `Asia/Kolkata`).
- Party size must be a whole number of at least 1; customer name and phone must not be blank.
- If two callers take the same slot at the same moment, a database unique index rejects one of them and that caller is offered alternatives.
- Reservation IDs are 6 random digits, easy to say aloud.

## Database schema

| Table | Purpose |
|---|---|
| `restaurants` | Name, opening and closing time, `slot_duration` in minutes |
| `tables` | Physical tables per restaurant, each with a `capacity` |
| `reservations` | Bookings: customer, date, time, party size, `status`, `slot_key` |
| `calls` | One row per call: phone, start and end time, status, summary, transcript, booking outcomes |
| `call_events` | Optional ordered per-call events, used only by `services/call_log.py` |

Key points:

- `reservations.status` is one of `confirmed`, `cancelled`, `completed`, `no_show`.
- `slot_key` is `{table_id}_{date}_{time}`.
- A **partial unique index** on `reservations(slot_key) WHERE status = 'confirmed'` stops two confirmed bookings taking the same table slot while still allowing a cancelled slot to be rebooked.

See [`db/database.md`](db/database.md) for more detail on the data layer.

## Safety and guardrails

- **Confirmation first:** the prompt requires explicit spoken confirmation before any booking, cancellation or reschedule. Silence or an ambiguous "yes" is not consent.
- **No invented data:** the model must not invent phone numbers, availability, reservation IDs or tool results.
- **Ownership check in code:** cancel and reschedule are rejected unless the reservation ID and phone number match, so the check does not depend on the model behaving.
- **No bulk cancellation:** "cancel all my reservations" does not cancel everything automatically; the caller must pick specific bookings and confirm.
- **Tool errors are contained:** if a tool fails, the caller hears a friendly message and the error goes to the log.

## Testing

```bash
uv run pytest -v
```

- `tests/test_reservations.py` runs against your **real Supabase project**, so it needs network access and a valid `.env`. It creates an isolated restaurant (`test_r`) with two tables and deletes it afterwards, so your real data is not touched.
- Run a subset with `-k`, for example `uv run pytest -v -k reschedule`.
- Covered: availability rules, booking and table choice, overlaps and back-to-back slots, cancellation and rebooking, rescheduling, lookup by phone, call logging, input validation and the unique index.
- `tests/test_tools.py` is a **manual smoke script**, not a pytest test. It writes to the database (it books a table on 2030-01-15). Run it with `uv run python -m tests.test_tools`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `PGRST125: Invalid path specified in request URL` | `SUPABASE_DATA_URL` has a path or trailing slash. Use only `https://<ref>.supabase.co`. |
| `new row violates row-level security policy` (`42501`) | You are using the anon key. Use the `service_role` key. |
| `RuntimeError: Missing SUPABASE_DATA_URL or SUPABASE_SERVICE_KEY` | Check the variable names in `.env`. |
| `KeyError: 'SARVAM_API_KEY'` or `'GROQ_API_KEY'` | Add the missing key to `.env`. |
| `Restaurant 'r1' was not found in the database` | Run `uv run python -m db.seed`. |
| `ModuleNotFoundError: bot...` | Run from the project root with `uv run python -m bot.bot`, not `python bot/bot.py`. |
| No filler speech during tool calls | Check the logs for an unknown-event warning; the event name may differ in your Pipecat version. |
| `DeprecationWarning: 'timeout' / 'verify' parameter is deprecated` | Comes from inside `supabase-py`; safe to ignore. |

## Security notes

- The service key bypasses Row Level Security. Use it only on the backend and never in client-side code.
- Keep Row Level Security **enabled** on all tables so the public anon key cannot read reservations or phone numbers.
- Never print or commit the service key. If it is exposed, rotate it in Supabase.
- Call transcripts contain customer phone numbers and names. Restrict access to the `calls` table and decide a retention period.

## Known limitations

- **Single restaurant:** `RESTAURANT_ID = "r1"` lives in `bot/config.py` and the prompt names "Demo Bistro".
- **Phone number is not verified:** it is collected by voice, so ownership checks rely on the caller knowing both the reservation ID and phone number.
- **Rare race condition:** two callers booking *different but overlapping* start times at the same instant could both succeed. A Postgres range-exclusion constraint would remove this.
- **Timezone is a constant** in `services/reservations.py`. Move it to the `restaurants` table for multi-timezone support.
- **Per-event logging is not connected:** `services/call_log.py` (`CallLogger` and the `call_events` table) is not started in the pipeline yet. Call records are saved at the end of the call by `services/call_record.py`.
- **English (India) only** for now.

## Roadmap

- Start `CallLogger` in the pipeline for live per-event logging
- Multi-restaurant support (resolve the restaurant per call or per number)
- Telephony transport (for example Twilio) so real phone calls can reach the agent
- Hindi and other Indian languages using Sarvam's multilingual models
- SMS or WhatsApp confirmation after booking
- Staff dashboard for viewing reservations and call transcripts
- Waitlist and no-show handling
- Skip the filler phrase when a tool finishes very quickly