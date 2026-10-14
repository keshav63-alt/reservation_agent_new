# bot/restaurant_info.py
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from services import reservations as rs

def _format_time(value) -> str:
    """Convert a database time into HH:MM."""
    return str(value)[:5]

def build_restaurant_info(restaurant_id: str) -> str:
    """Build the restaurant facts (hours, slot rules, calendar) for the system prompt."""

    rest = rs.get_restaurant(restaurant_id)

    if not rest:
        raise ValueError(
            f"Restaurant {restaurant_id!r} was not found in the database."
        )

    name = rest.get("name") or "our restaurant"
    opening_time = _format_time(rest.get("opening_time", "12:00"))
    closing_time = _format_time(rest.get("closing_time", "23:00"))
    slot_duration = int(rest.get("slot_duration", 90))

    timezone_name = (
        rest.get("timezone")
        or getattr(rs, "TZ", "Asia/Kolkata")
    )
    timezone = ZoneInfo(timezone_name)

    now = datetime.now(timezone)

    open_minutes = (
        int(opening_time[:2]) * 60
        + int(opening_time[3:5])
    )
    close_minutes = (
        int(closing_time[:2]) * 60
        + int(closing_time[3:5])
    )

    last_start_minutes = close_minutes - slot_duration

    if last_start_minutes < open_minutes:
        last_start = "unavailable"
    else:
        last_start = (
            f"{last_start_minutes // 60:02d}:"
            f"{last_start_minutes % 60:02d}"
        )

    calendar_lines = []

    for offset in range(8):
        day = now + timedelta(days=offset)

        label = (
            " (today)" if offset == 0
            else " (tomorrow)" if offset == 1
            else ""
        )

        calendar_lines.append(
            f"- {day:%A} {day.day} {day:%B} "
            f"= {day:%Y-%m-%d}{label}"
        )

    restaurant_info = f"""
Restaurant name: {name}
Restaurant ID: {restaurant_id}
Restaurant timezone: {timezone_name}

Opening time: {opening_time}
Closing time: {closing_time}
Reservation duration: {slot_duration} minutes
Last valid booking start time: {last_start}
Allowed booking intervals: On the hour and half hour.

Current date: {now:%Y-%m-%d}
Current day: {now:%A}
Current local time: {now:%I:%M %p}

Use the following calendar to resolve relative dates:
{chr(10).join(calendar_lines)}

Only use the restaurant information and tool results supplied by the system.
Do not invent availability, booking details, policies, or successful operations.
""".strip()

    return restaurant_info
