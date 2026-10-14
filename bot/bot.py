# bot.py
#
# Browser audio -> Sarvam STT -> Groq LLM + reservation tools
#              -> Sarvam TTS -> Browser audio
#
# Run from project root:
#   uv run bot.py

import asyncio
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from loguru import logger

from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.sarvam.stt import SarvamRealtimeSTTService
from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.transports.base_transport import TransportParams
from pipecat.turns.user_start import ExternalUserTurnStartStrategy
from pipecat.turns.user_stop import ExternalUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from pipecat.services.nvidia.llm import NvidiaLLMService        

from pipecat.observers.service_metrics_observer import ServiceMetricsObserver
from pipecat.pipeline.worker import PipelineParams, PipelineWorker

import json
from datetime import datetime, timezone
from db.connection import supabase


from services import reservations as rs
from bot.prompts import get_voice_prompt
from bot.tools import (
    check_availability,
    reschedule_reservation,
    cancel_reservation,
    book_reservation,
    find_my_reservations

)

load_dotenv(override=True)

RESTAURANT_ID = "r1"



from pipecat.observers.service_metrics_observer import ServiceMetricsObserver
from pipecat.pipeline.worker import PipelineParams, PipelineWorker




class SessionMetricsTracker:
    """Accumulates usage tokens and latencies across the call."""

    def __init__(self):
        self.llm_prompt_tokens = 0
        self.llm_completion_tokens = 0
        self.llm_total_tokens = 0
        self.tts_characters = 0
        self.stt_audio_seconds = 0.0

        self.llm_ttfb: list[float] = []
        self.tts_ttfb: list[float] = []

    def record_usage(self, record):
        kind = str(getattr(record, "kind", "")).lower()
        p_tokens = getattr(record, "prompt_tokens", None)
        c_tokens = getattr(record, "completion_tokens", None)
        t_tokens = getattr(record, "total_tokens", None)

        if "llm" in kind or p_tokens is not None or c_tokens is not None:
            if p_tokens:
                self.llm_prompt_tokens += p_tokens
            if c_tokens:
                self.llm_completion_tokens += c_tokens
            if t_tokens:
                self.llm_total_tokens += t_tokens
            elif p_tokens or c_tokens:
                self.llm_total_tokens += (p_tokens or 0) + (c_tokens or 0)

        chars = getattr(record, "characters", None)
        if "tts" in kind or chars is not None:
            if chars:
                self.tts_characters += chars

        audio_secs = getattr(record, "audio_seconds", None)
        if "stt" in kind or audio_secs is not None:
            if audio_secs:
                self.stt_audio_seconds += audio_secs

    def record_latency(self, record):
        processor = str(getattr(record, "processor", "")).lower()
        kind = str(getattr(record, "kind", "")).lower()
        secs = getattr(record, "seconds", 0.0)

        if "llm" in processor or "groq" in processor:
            if "ttfb" in kind:
                self.llm_ttfb.append(secs)
        elif "tts" in processor or "sarvam" in processor:
            if "ttfb" in kind:
                self.tts_ttfb.append(secs)

    def print_summary(self):
        def avg(lst):
            return round(sum(lst) / len(lst), 3) if lst else 0.0

        border = "=" * 62
        print(f"\n{border}")
        print("📊 CALL SESSION METRICS & USAGE REPORT")
        print(border)
        print(f"🔹 LLM (Groq openai/gpt-oss-20b):")
        print(f"   • Prompt Tokens:      {self.llm_prompt_tokens:,}")
        print(f"   • Completion Tokens:  {self.llm_completion_tokens:,}")
        print(f"   • Total Tokens:       {self.llm_total_tokens:,}")
        print(
            f"   • TTFB Latency:       avg {avg(self.llm_ttfb)}s (calls: {len(self.llm_ttfb)})"
        )
        print(f"🔹 TTS (Sarvam bulbul:v3):")
        print(f"   • Synthesized Chars:  {self.tts_characters:,}")
        print(
            f"   • TTFB Latency:       avg {avg(self.tts_ttfb)}s (turns: {len(self.tts_ttfb)})"
        )
        print(f"🔹 STT (Sarvam Realtime):")
        print(f"   • Audio Transcribed:  {self.stt_audio_seconds:.1f} seconds")
        print(f"{border}\n")



def _format_time(value) -> str:
    """Convert a database time into HH:MM."""
    return str(value)[:5]


def build_restaurant_info(restaurant_id: str) -> tuple[str, str]:
    """Build restaurant facts and a greeting for this call."""

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

async def bot(runner_args: RunnerArguments):
    call_start_time = datetime.now(timezone.utc).isoformat()

    transport = await create_transport(
        runner_args,
        {
            "webrtc": lambda: TransportParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
            ),
        },
    )

    restaurant_info = await asyncio.to_thread(
        build_restaurant_info,
        RESTAURANT_ID,
    )


    # 1. Initialize tracker and observer
    metrics_tracker = SessionMetricsTracker()
    metrics_observer = ServiceMetricsObserver()

    @metrics_observer.event_handler("on_service_latency")
    async def on_service_latency(observer, record):
        metrics_tracker.record_latency(record)

    @metrics_observer.event_handler("on_service_usage")
    async def on_service_usage(observer, record):
        metrics_tracker.record_usage(record)


    system_prompt = get_voice_prompt(restaurant_info)
    messages = [{"role": "system", "content": system_prompt}]
    context = LLMContext(messages, tools=[
                                            check_availability,
                                            book_reservation,
                                            cancel_reservation,
                                            reschedule_reservation,
                                            find_my_reservations,
                                        ]
)

    stt = SarvamRealtimeSTTService(
        api_key=os.environ["SARVAM_API_KEY"],
        sample_rate=16000,
        settings=SarvamRealtimeSTTService.Settings(
            language_code="en-IN",
            mode="transcribe",
            stream_type="fast",
        ),
        should_interrupt=False,
    )

    llm = GroqLLMService(
        api_key=os.environ["GROQ_API_KEY"],
        settings=GroqLLMService.Settings(
            model="openai/gpt-oss-20b",
            reasoning_effort="low",
             max_tokens=300,
             temperature = 0.3,
        ),
    )

   

    tts = SarvamTTSService(
    api_key=os.environ["SARVAM_API_KEY"],
    settings=SarvamTTSService.Settings(
        model="bulbul:v3",
        voice="shubh",
        language="en-IN",
        pace=1.0,
        min_buffer_size=10,
        max_chunk_length=250,
    ),
    sample_rate=24000,
)

    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                start=[ExternalUserTurnStartStrategy()],
                stop=[
                    ExternalUserTurnStopStrategy(
                        timeout=0.5,               # 1000ms of silence indicates end of turn
                        wait_for_transcript=True, # Don't wait up to 3s for final transcript flag
                    )
                ],
            ),
            user_turn_stop_timeout=1.2,           # Fallback max wait down from 3.0s to 1.2s
        ),
    )
    # # ---------- Filler speech on tool calls ----------
    # @llm.event_handler("on_function_calls_started")
    # async def on_function_calls_started(service, function_calls):
    #     await tts.queue_frame(TTSSpeakFrame("Let me check that for you."))

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,  # Enables LLM tokens & TTS characters
        ),
        observers=[metrics_observer],
    )
    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)

    # Helper function to save the transcript to Supabase
    async def save_call_record():
        call_end_time = datetime.now(timezone.utc).isoformat()
        transcript_lines = []
        customer_phone = "unknown"
        appointments = []

        for msg in context.messages:
            role = msg.get("role")
            content = msg.get("content")
            tool_calls = msg.get("tool_calls")

            if role == "user" and content:
                transcript_lines.append(f"Caller: {content}")
            elif role == "assistant" and content:
                transcript_lines.append(f"Agent: {content}")
            elif role == "assistant" and tool_calls:
                for tc in tool_calls:
                    fn_args = tc.get("function", {}).get("arguments", "{}")
                    try:
                        args_dict = json.loads(fn_args) if isinstance(fn_args, str) else fn_args
                        if "customer_phone" in args_dict and args_dict["customer_phone"] != "<missing>":
                            customer_phone = args_dict["customer_phone"]
                    except Exception:
                        pass
            elif role == "tool":
                try:
                    tool_data = json.loads(msg.get("content", "{}"))
                    if tool_data.get("ok") and "reservation_id" in tool_data:
                        appointments.append(tool_data)
                except Exception:
                    pass

        full_transcript = "\n".join(transcript_lines)

        record = {
            "phone_no": customer_phone,
            "start_time": call_start_time,
            "end_time": call_end_time,
            "status": "completed",
            "restaurant_id": RESTAURANT_ID,
            "appointment_details": json.dumps(appointments) if appointments else None,
            "summary": f"Reservation: {appointments[0]['reservation_id']}" if appointments else "Inquiry",
            "transcript": full_transcript,
        }

        try:
            await asyncio.to_thread(lambda: supabase.table("calls").insert(record).execute())
            logger.info("Call transcript successfully saved to Supabase")
        except Exception as e:
            logger.error(f"Failed to save call record: {e}")

    
    

    # Register event handlers ONCE only
    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Caller connected")
        await worker.queue_frame(TTSSpeakFrame(
            "Hello! I'm Rosi, your restaurant receptionist. "
            "I can help answer questions, book, cancel, or "
            "reschedule reservations. How can I help you today?"))  

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        metrics_tracker.print_summary()
        await worker.cancel()
        await runner.cancel(reason="Caller disconnected")
    
    
    await runner.add_workers(worker)
    await runner.run()

if __name__ == "__main__":
    from pipecat.runner.run import main

    main()