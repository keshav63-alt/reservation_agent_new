# bot/tools.py
import asyncio
import re
from typing import Optional

from loguru import logger
from pipecat.services.llm_service import FunctionCallParams
from bot.config import DEFAULT_COUNTRY_CODE, RESTAURANT_ID
from services.call_log import current_call_logger
from services import reservations as rs


# ---------- helpers ----------
def _int(value):
    """LLMs sometimes send numbers as strings. Returns None if not a number."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _clean_phone(phone) -> str:
    """'98765 43210' -> '+919876543210'. Leaves blank input blank."""
    s = re.sub(r"[^\d+]", "", str(phone or ""))
    if s and not s.startswith("+") and len(s) == 10:
        s = DEFAULT_COUNTRY_CODE + s
    return s



def _same_phone(a, b) -> bool:
    da = re.sub(r"\D", "", str(a or ""))[-10:]
    db_ = re.sub(r"\D", "", str(b or ""))[-10:]
    return bool(da) and da == db_




async def _run(params: FunctionCallParams, fn, *args, **kwargs):
    cl = current_call_logger.get()
    if cl:
        cl.log("tool_call", params.function_name, {"arguments": dict(params.arguments)})
    try:
        result = await asyncio.to_thread(fn, *args, **kwargs)
    except Exception as e:
        logger.bind(no_db=True).exception(f"tool {params.function_name} failed")
        result = {"ok": False, "message": "Something went wrong on our side. Please try again."}
        if cl:
            cl.log("error", f"tool {params.function_name} failed: {e!r}")
    if cl:
        cl.log("tool_result", params.function_name, {"result": result})
        cl.record_outcome(params.function_name, result, params.arguments)
    await params.result_callback(result)


async def _verify_owner(params: FunctionCallParams, reservation_id, phone) -> bool:
    res = await asyncio.to_thread(rs.get_reservation, str(reservation_id).strip())
    if not res or not _same_phone(res["customer_phone"], phone):
        result = {"ok": False,
                  "message": "I couldn't find a reservation with that ID and phone number."}
        cl = current_call_logger.get()
        if cl:
            cl.log("tool_result", params.function_name,
                   {"stage": "ownership_check", "arguments": dict(params.arguments), "result": result})
        await params.result_callback(result)
        return False
    return True


# ---------- tools the LLM can call ----------
async def check_availability(
    params: FunctionCallParams,
    date: str = None,
    time: str = None,
    party_size: Optional[int] = None,
):
    """Check whether a table is free. Call this before offering or booking a time."""
    if not date or not time or not party_size:
        await params.result_callback({
            "ok": False,
            "message": "Missing details. Please ask the caller for the date, time, and number of guests before checking availability."
        })
        return
    await _run(params, rs.check_availability, RESTAURANT_ID, str(date), str(time), _int(party_size))

async def book_reservation(
    params: FunctionCallParams,
    customer_name: str,
    customer_phone: str,
    date: str,
    time: str,
    party_size: int,
    special_requests: Optional[str] = None,
):
    """Book a table. Only call this after the caller has confirmed all details out loud.

    Args:
        customer_name: Name the booking is under.
        customer_phone: The caller's phone number.
        date: Reservation date as YYYY-MM-DD.
        time: Start time in 24-hour HH:MM.
        party_size: Number of guests.
        special_requests: Optional notes such as a birthday or an allergy.
    """
    await _run(
        params, rs.book_reservation,
        RESTAURANT_ID, (customer_name or "").strip(), _clean_phone(customer_phone),
        date, time, _int(party_size), special_requests=special_requests,
    )


async def cancel_reservation(
    params: FunctionCallParams,
    reservation_id: str,
    customer_phone: str,
    reason: Optional[str] = None,
):
    """Cancel an existing reservation after the caller confirms they want to cancel.

    Args:
        reservation_id: The 6-digit reservation ID.
        customer_phone: Phone number the booking was made with, to verify the caller.
        reason: Optional reason for cancelling.
    """
    if not await _verify_owner(params, reservation_id, customer_phone):
        return
    await _run(params, rs.cancel_reservation, str(reservation_id).strip(), reason)


async def reschedule_reservation(
    params: FunctionCallParams,
    reservation_id: str,
    customer_phone: str,
    new_date: str,
    new_time: str,
    new_party_size: Optional[int] = None,
):
    """Move an existing reservation to a new date or time, optionally changing party size.

    Args:
        reservation_id: The 6-digit reservation ID.
        customer_phone: Phone number the booking was made with, to verify the caller.
        new_date: New date as YYYY-MM-DD.
        new_time: New start time in 24-hour HH:MM.
        new_party_size: New number of guests, only if the caller wants to change it.
    """
    if not await _verify_owner(params, reservation_id, customer_phone):
        return
    size = None if new_party_size is None else _int(new_party_size)
    await _run(params, rs.reschedule_reservation, str(reservation_id).strip(),
               new_date, new_time, size)


async def find_my_reservations(params: FunctionCallParams, customer_phone: str):
    """Look up a caller's upcoming reservations. 
    ONLY call this if the caller explicitly asks to check or look up an existing reservation,
    AND you already have their valid 10-digit phone number. Do not call with placeholder or missing values.

    Args:
        customer_phone: The caller's 10-digit phone number.
    """
    if not customer_phone or "<missing>" in customer_phone:
        await params.result_callback({"ok": False, "message": "Phone number required."})
        return

    def _lookup():
        rows = rs.find_reservations_by_phone(_clean_phone(customer_phone))
        return {
            "ok": True,
            "reservations": [
                {
                    "reservation_id": r["reservation_id"],
                    "date": r["reservation_date"],
                    "time": r["reservation_time"][:5],
                    "party_size": r["party_size"],
                }
                for r in rows
            ],
        }
    await _run(params, _lookup)


TOOLS = [
    check_availability,
    book_reservation,
    cancel_reservation,
    reschedule_reservation,
    find_my_reservations,
]