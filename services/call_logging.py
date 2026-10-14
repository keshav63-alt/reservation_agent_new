# services/call_log.py
"""Save every call (calls table) and everything that happens in it (call_events table).

Design rules:
  * Logging must never slow down or crash a live call, so log() only puts an item on a
    queue. A background task writes the queue to Supabase in small batches.
  * If the database is down, the call carries on and the failure goes to the normal log.
  * Items are numbered in the order they happen, so a transcript can be rebuilt exactly.
"""
import asyncio
import json
import threading
from contextvars import ContextVar
from datetime import datetime, timezone

from loguru import logger

from db.connection import supabase

# The tools and the loguru sink find "the call I belong to" through this variable.
current_call_logger: ContextVar["CallLogger | None"] = ContextVar(
    "current_call_logger", default=None
)

BATCH_SIZE = 25
OUTCOME_TOOLS = {"book_reservation", "cancel_reservation", "reschedule_reservation"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CallLogger:
    def __init__(self, restaurant_id: str, phone_no: str | None = None):
        self.restaurant_id = restaurant_id
        self.phone_no = phone_no
        self.call_id: int | None = None
        self.status = "completed"          # set to "failed" if the call crashes

        self._seq = 0
        self._lock = threading.Lock()      # log() may be called from worker threads
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue | None = None
        self._task: asyncio.Task | None = None
        self._sink_id: int | None = None
        self._outcomes: list[dict] = []

    # ---------- lifecycle ----------
    async def start(self) -> None:
        """Create the calls row. Call this BEFORE current_call_logger.set(...)."""
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        try:
            res = await asyncio.to_thread(
                lambda: supabase.table("calls").insert({
                    "phone_no": self.phone_no,
                    "start_time": _now(),
                    "status": "in_progress",
                    "restaurant_id": self.restaurant_id,
                }).execute()
            )
            self.call_id = res.data[0]["id"]
        except Exception:
            logger.bind(no_db=True).exception(
                "Could not create the call record; this call will not be saved"
            )
            return

        self._task = asyncio.create_task(self._worker())
        # Copy WARNING/ERROR log lines produced during this call into call_events.
        self._sink_id = logger.add(self._sink, level="WARNING", filter=self._filter)

    async def close(self) -> None:
        """Flush everything and mark the call finished."""
        if self.call_id is None:
            return
        if self._sink_id is not None:
            logger.remove(self._sink_id)
            self._sink_id = None

        self.log("info", "call ended", {"status": self.status})
        self._loop.call_soon_threadsafe(self._queue.put_nowait, None)   # stop signal
        try:
            await asyncio.wait_for(self._task, timeout=15)
        except Exception:
            logger.bind(no_db=True).warning("Timed out flushing call events")

        update = {"end_time": _now(), "status": self.status}
        if self._outcomes:
            update["appointment_details"] = json.dumps(self._outcomes)
        try:
            await asyncio.to_thread(
                lambda: supabase.table("calls").update(update).eq("id", self.call_id).execute()
            )
        except Exception:
            logger.bind(no_db=True).exception("Could not finish the call record")

    # ---------- logging ----------
    def log(self, kind: str, content: str | None = None, data: dict | None = None) -> None:
        """kind: user | assistant | tool_call | tool_result | error | info. Never blocks."""
        if self.call_id is None or self._loop is None:
            return
        with self._lock:
            self._seq += 1
            seq = self._seq
        item = {
            "call_id": self.call_id,
            "seq": seq,
            "ts": _now(),
            "kind": kind,
            "content": content,
            "data": data,
        }
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, item)
        except RuntimeError:               # event loop already closed
            pass

    def record_outcome(self, tool_name: str, result, arguments=None) -> None:
        """Remember successful bookings/cancellations/changes for calls.appointment_details."""
        if tool_name not in OUTCOME_TOOLS or not isinstance(result, dict) or not result.get("ok"):
            return
        args = arguments or {}
        self._outcomes.append({
            "action": tool_name,
            "reservation_id": result.get("reservation_id") or args.get("reservation_id"),
            "date": result.get("date") or args.get("new_date"),
            "time": result.get("time") or args.get("new_time"),
            "party_size": result.get("party_size"),
        })

    # ---------- internals ----------
    def _filter(self, record) -> bool:
        return not record["extra"].get("no_db") and current_call_logger.get() is self

    def _sink(self, message) -> None:
        r = message.record
        self.log(
            "error",
            r["message"][:2000],
            {"level": r["level"].name, "where": f'{r["name"]}:{r["function"]}:{r["line"]}'},
        )

    async def _worker(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            batch, stop = [item], False
            while len(batch) < BATCH_SIZE and not self._queue.empty():
                nxt = self._queue.get_nowait()
                if nxt is None:
                    stop = True
                    break
                batch.append(nxt)
            try:
                await asyncio.to_thread(
                    lambda b=batch: supabase.table("call_events").insert(b).execute()
                )
            except Exception:
                logger.bind(no_db=True).warning(f"Could not save {len(batch)} call events")
            if stop:
                return