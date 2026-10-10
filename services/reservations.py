# db/reservations.py
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

from db.connection import supabase

TZ = "Asia/Kolkata"
STEP_MIN = 30       # bookings allowed on the hour / half hour
# PARTY_ERR = "Party size must be a whole number of at least 1."


# ---------- helpers ----------
def _hm(t: str) -> str:
    return t[:5]                                   # "20:00:00" -> "20:00"

def _minutes(t: str) -> int:
    h, m = _hm(t).split(":")
    return int(h) * 60 + int(m)

def _fmt(mins: int) -> str:
    return f"{mins // 60:02d}:{mins % 60:02d}"

def _new_id() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(6))   # easy to say aloud

def _is_dup(e: Exception) -> bool:
    return getattr(e, "code", None) == "23505"     # Postgres unique violation

def _valid_party(n) -> bool:
    return isinstance(n, int) and not isinstance(n, bool) and n > 0

PARTY_ERR = "Party size must be a whole number of at least 1."

def _blank(s) -> bool:
    return not isinstance(s, str) or not s.strip()



# ---------- data loading ----------
def get_restaurant(restaurant_id: str):
    rows = (supabase.table("restaurants").select("*")
            .eq("restaurant_id", restaurant_id).execute().data)
    return rows[0] if rows else None

def _load_day(restaurant_id: str, date: str, exclude_id: str | None = None):
    tables = (supabase.table("tables").select("*")
              .eq("restaurant_id", restaurant_id).execute().data)
    booked = (supabase.table("reservations")
              .select("reservation_id, table_id, reservation_time")
              .eq("restaurant_id", restaurant_id)
              .eq("reservation_date", date)
              .eq("status", "confirmed").execute().data)
    booked = [r for r in booked if r["reservation_id"] != exclude_id]
    return tables, booked


# ---------- logic ----------
def _validate(rest: dict, date: str, time: str):
    """Return an error message, or None if the date/time is bookable."""
    try:
        when = datetime.strptime(f"{date} {_hm(time)}", "%Y-%m-%d %H:%M")
    except ValueError:
        return "Invalid date or time format."
    start = when.hour * 60 + when.minute
    dur = rest["slot_duration"]
    open_m, close_m = _minutes(rest["opening_time"]), _minutes(rest["closing_time"])
    if start % STEP_MIN:
        return "Bookings are available on the hour or half hour only."
    if start < open_m or start + dur > close_m:
        return f"Bookings run from {_hm(rest['opening_time'])} to {_fmt(close_m - dur)}."
    if when < datetime.now(ZoneInfo(TZ)).replace(tzinfo=None):
        return "That time is in the past."
    return None

def _pick_table(tables, booked, start, dur, party):
    """Smallest table that fits the party and has no overlapping booking."""
    for t in sorted((t for t in tables if t["capacity"] >= party),
                    key=lambda t: t["capacity"]):
        clash = any(
            r["table_id"] == t["table_id"]
            and start < _minutes(r["reservation_time"]) + dur
            and _minutes(r["reservation_time"]) < start + dur
            for r in booked
        )
        if not clash:
            return t
    return None

def _alternatives(rest, tables, booked, party, near_start, limit=3):
    dur = rest["slot_duration"]
    open_m, close_m = _minutes(rest["opening_time"]), _minutes(rest["closing_time"])
    options = [m for m in range(open_m, close_m - dur + 1, STEP_MIN)
               if m != near_start and _pick_table(tables, booked, m, dur, party)]
    options.sort(key=lambda m: abs(m - near_start))
    return [_fmt(m) for m in options[:limit]]


# ---------- public functions (the LLM tools call these) ----------
def check_availability(restaurant_id, date, time, party_size):
    if not _valid_party(party_size):
        return {"ok": False, "available": False, "message": PARTY_ERR}

    rest = get_restaurant(restaurant_id)
    if not rest:
        return {"ok": False, "message": "Restaurant not found."}
    err = _validate(rest, date, time)
    if err:
        return {"ok": False, "available": False, "message": err}

    tables, booked = _load_day(restaurant_id, date)
    if party_size > max((t["capacity"] for t in tables), default=0):
        return {"ok": True, "available": False,
                "message": "Party is larger than our biggest table."}

    start, dur = _minutes(time), rest["slot_duration"]
    if _pick_table(tables, booked, start, dur, party_size):
        return {"ok": True, "available": True, "message": "A table is available."}
    
    if not _valid_party(party_size):
        return {"ok": False, "available": False, "message": PARTY_ERR}
    
    return {"ok": True, "available": False,
            "message": "That time is fully booked.",
            "alternatives": _alternatives(rest, tables, booked, party_size, start)}


def book_reservation(restaurant_id, name, phone, date, time, party_size,
                     email=None, special_requests=None):
    rest = get_restaurant(restaurant_id)

    if not _valid_party(party_size):
        return {"ok": False, "message": PARTY_ERR}
    if _blank(name):
        return {"ok": False, "message": "Customer name is required."}
    if _blank(phone):
        return {"ok": False, "message": "Customer phone number is required."}
    name, phone = name.strip(), phone.strip()

    if not rest:
        return {"ok": False, "message": "Restaurant not found."}
    err = _validate(rest, date, time)
    if err:
        return {"ok": False, "message": err}

    tables, booked = _load_day(restaurant_id, date)
    start, dur = _minutes(time), rest["slot_duration"]
    table = _pick_table(tables, booked, start, dur, party_size)
    if not table:
        return {"ok": False, "message": "No table available at that time.",
                "alternatives": _alternatives(rest, tables, booked, party_size, start)}

    time = _hm(time)
    for _ in range(3):                                   # retry if the ID collides
        rid = _new_id()
        try:
            supabase.table("reservations").insert({
                "reservation_id": rid,
                "restaurant_id": restaurant_id,
                "table_id": table["table_id"],
                "customer_name": name,
                "customer_phone": phone,
                "customer_email": email,
                "reservation_date": date,
                "reservation_time": time,
                "party_size": party_size,
                "special_requests": special_requests,
                "slot_key": f"{table['table_id']}_{date}_{time}",
            }).execute()
            return {"ok": True, "reservation_id": rid,
                    "table": table["table_name"], "date": date, "time": time,
                    "party_size": party_size, "message": "Reservation confirmed."}
        except Exception as e:
            if not _is_dup(e):
                raise
            # duplicate = ID collision or someone just took the slot: re-check
            tables, booked = _load_day(restaurant_id, date)
            table = _pick_table(tables, booked, start, dur, party_size)
            if not table:
                break
    return {"ok": False, "message": "That slot was just taken.",
            "alternatives": _alternatives(rest, tables, booked, party_size, start)}


def get_reservation(reservation_id):
    rows = (supabase.table("reservations").select("*")
            .eq("reservation_id", reservation_id).execute().data)
    return rows[0] if rows else None


def find_reservations_by_phone(phone):
    return (supabase.table("reservations").select("*")
            .eq("customer_phone", phone).eq("status", "confirmed")
            .order("reservation_date").execute().data)


def cancel_reservation(reservation_id, reason=None):
    res = get_reservation(reservation_id)
    if not res:
        return {"ok": False, "message": "Reservation not found."}
    if res["status"] != "confirmed":
        return {"ok": False, "message": f"Reservation is already {res['status']}."}
    supabase.table("reservations").update({
        "status": "cancelled", "cancellation_reason": reason,
    }).eq("reservation_id", reservation_id).execute()
    return {"ok": True, "message": "Reservation cancelled."}


def reschedule_reservation(reservation_id, new_date, new_time, new_party_size=None):


    res = get_reservation(reservation_id)

    if new_party_size is not None and not _valid_party(new_party_size):
        return {"ok": False, "message": PARTY_ERR}


    if not res or res["status"] != "confirmed":
        return {"ok": False, "message": "No active reservation found with that ID."}

    rest = get_restaurant(res["restaurant_id"])
    err = _validate(rest, new_date, new_time)
    if err:
        return {"ok": False, "message": err}

    # party = new_party_size or res["party_size"]
    party = res["party_size"] if new_party_size is None else new_party_size

    # exclude this reservation so it doesn't block its own new slot
    tables, booked = _load_day(res["restaurant_id"], new_date, exclude_id=reservation_id)
    start, dur = _minutes(new_time), rest["slot_duration"]
    table = _pick_table(tables, booked, start, dur, party)
    if not table:
        return {"ok": False, "message": "No table available at the new time.",
                "alternatives": _alternatives(rest, tables, booked, party, start)}

    new_time = _hm(new_time)
    try:
        supabase.table("reservations").update({
            "table_id": table["table_id"],
            "reservation_date": new_date,
            "reservation_time": new_time,
            "party_size": party,
            "slot_key": f"{table['table_id']}_{new_date}_{new_time}",
        }).eq("reservation_id", reservation_id).execute()
    except Exception as e:
        if _is_dup(e):
            return {"ok": False, "message": "That slot was just taken."}
        raise
    return {"ok": True, "reservation_id": reservation_id, "table": table["table_name"],
            "date": new_date, "time": new_time, "party_size": party,
            "message": "Reservation rescheduled."}


def log_call(phone_no, start_time, end_time, status, restaurant_id,
             appointment_details=None, summary=None, tokens_used=None):
    supabase.table("calls").insert({
        "phone_no": phone_no, "start_time": start_time, "end_time": end_time,
        "status": status, "restaurant_id": restaurant_id,
        "appointment_details": appointment_details,
        "summary": summary, "tokens_used": tokens_used,
    }).execute()
    