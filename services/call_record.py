# services/call_record.py
import asyncio
import json
from datetime import datetime, timezone

from loguru import logger

from db.connection import supabase


async def save_call_record(messages: list, start_time: str, restaurant_id: str) -> None:
    """Build the transcript from the LLM context and save it to the calls table."""
    transcript_lines, appointments = [], []
    customer_phone = "unknown"

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        if role == "user" and content:
            transcript_lines.append(f"Caller: {content}")
        elif role == "assistant" and content:
            transcript_lines.append(f"Agent: {content}")
        elif role == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                args = tc.get("function", {}).get("arguments", "{}")
                try:
                    args = json.loads(args) if isinstance(args, str) else args
                    phone = args.get("customer_phone")
                    if phone and phone != "<missing>":
                        customer_phone = phone
                except Exception:
                    pass
        elif role == "tool":
            try:
                data = json.loads(content or "{}")
                if data.get("ok") and "reservation_id" in data:
                    appointments.append(data)
            except Exception:
                pass

    record = {
        "phone_no": customer_phone,
        "start_time": start_time,
        "end_time": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "restaurant_id": restaurant_id,
        "appointment_details": json.dumps(appointments) if appointments else None,
        "summary": f"Reservation: {appointments[0]['reservation_id']}" if appointments else "Inquiry",
        "transcript": "\n".join(transcript_lines),
    }

    try:
        await asyncio.to_thread(lambda: supabase.table("calls").insert(record).execute())
        logger.info("Call transcript successfully saved to Supabase")
    except Exception as e:
        logger.error(f"Failed to save call record: {e}")