# # bot.py  (project root)
# #
# # Voice pipeline for the restaurant reservation agent:
# #   browser audio -> Sarvam STT -> Groq LLM (+ reservation tools) -> Sarvam TTS -> browser
# #
# # Run from the project root:   uv run bot.py
# # Then open the URL it prints (http://localhost:7860) and click Connect.
# import asyncio
# import os

# from dotenv import load_dotenv
# from loguru import logger

# from pipecat.frames.frames import TTSSpeakFrame
# from pipecat.pipeline.pipeline import Pipeline
# from pipecat.pipeline.worker import PipelineParams, PipelineWorker
# from pipecat.processors.aggregators.llm_context import LLMContext
# from pipecat.processors.aggregators.llm_response_universal import (
#     LLMContextAggregatorPair,
#     LLMUserAggregatorParams,
# )
# from pipecat.runner.types import RunnerArguments
# from pipecat.runner.utils import create_transport
# from pipecat.services.groq.llm import GroqLLMService
# from pipecat.services.sarvam.stt import SarvamRealtimeSTTService
# from pipecat.services.sarvam.tts import SarvamTTSService
# from pipecat.transports.base_transport import TransportParams
# from pipecat.turns.user_start import ExternalUserTurnStartStrategy
# from pipecat.turns.user_stop import ExternalUserTurnStopStrategy
# from pipecat.turns.user_turn_strategies import UserTurnStrategies
# from pipecat.workers.runner import WorkerRunner

# from bot.prompts import  get_voice_prompt
# from bot.tools import TOOLS

# load_dotenv(override=True)

# RESTAURANT_ID = "r1"


# async def bot(runner_args: RunnerArguments):
#     """Entry point: Pipecat calls this once per connected caller."""

#     # ---------- transport (browser WebRTC for testing) ----------
#     transport = await create_transport(
#         runner_args,
#         {
#             "webrtc": lambda: TransportParams(
#                 audio_in_enabled=True,
#                 audio_out_enabled=True,
#             ),
#         },
#     )

#     # ---------- speech to text ----------
#     # Sarvam runs its own server-side VAD, so there is NO Silero VAD in this pipeline.
#     stt = SarvamRealtimeSTTService(
#         api_key=os.environ["SARVAM_API_KEY"],
#         sample_rate=16000,
#         settings=SarvamRealtimeSTTService.Settings(
#             language_code="en-IN",   # "hi-IN", "te-IN", or "auto" for other callers
#             mode="transcribe",       # "codemix" suits Hinglish
#             stream_type="fast",      # lowest latency option
#         ),
#         should_interrupt=True,       # caller can talk over the bot
#     )

#     # ---------- LLM ----------
#     llm = GroqLLMService(
#         api_key=os.environ["GROQ_API_KEY"],
#         settings=GroqLLMService.Settings(model="openai/gpt-oss-120b"),
#     )

#     # ---------- text to speech ----------
#     tts = SarvamTTSService(
#     api_key=os.environ["SARVAM_API_KEY"],
#     settings=SarvamTTSService.Settings(
#         model="bulbul:v3",
#         voice="shubh",
#         language="en-IN",
#     ),
#     sample_rate=24000,
# )

#     # ---------- conversation context ----------
#     # Built per call so "today" is correct. Runs in a thread because it queries Supabase.
#     # system_prompt, greeting = await asyncio.to_thread(
#     #     lambda: (build_system_prompt(RESTAURANT_ID), build_greeting(RESTAURANT_ID))
#     # )

#     messages ={"role": "system", "content": }
#     context = LLMContext(messages, tools=TOOLS)

#     user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
#         context,
#         user_params=LLMUserAggregatorParams(
#             # Sarvam's STT announces start/stop of speech itself, so use External strategies.
#             user_turn_strategies=UserTurnStrategies(
#                 start=[ExternalUserTurnStartStrategy()],
#                 stop=[ExternalUserTurnStopStrategy(timeout=0.2, wait_for_transcript=True)],
#             ),
#             user_turn_stop_timeout=3.0,
#         ),
#     )

#     # ---------- pipeline ----------
#     pipeline = Pipeline(
#         [
#             transport.input(),
#             stt,
#             user_aggregator,
#             llm,
#             tts,
#             transport.output(),
#             assistant_aggregator,
#         ]
#     )

#     worker = PipelineWorker(pipeline, params=PipelineParams(enable_metrics=True))

#     @transport.event_handler("on_client_connected")
#     async def on_client_connected(transport, client):
#         logger.info("Caller connected")
#         # Fixed greeting (no LLM round trip), and remember it in the context.
#         messages.append({"role": "assistant", "content": })
#         await worker.queue_frame(TTSSpeakFrame(greeting))

#     @transport.event_handler("on_client_disconnected")
#     async def on_client_disconnected(transport, client):
#         logger.info("Caller disconnected")
#         await worker.cancel()

#     runner = WorkerRunner()
#     await runner.add_workers(worker)
#     await runner.run()


# if __name__ == "__main__":
#     from pipecat.runner.run import main

#     main()


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

from services import reservations as rs
from bot.prompts import get_voice_prompt
from bot.tools import TOOLS

load_dotenv(override=True)

RESTAURANT_ID = "r1"


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

    greeting = (
        f"Hello, thank you for calling {name}. "
        "How can I help you today?"
    )

    return restaurant_info, greeting


async def bot(runner_args: RunnerArguments):
    """Entry point for each connected caller."""

    # ---------- Transport ----------
    transport = await create_transport(
        runner_args,
        {
            "webrtc": lambda: TransportParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
            ),
        },
    )

    # ---------- Load restaurant data and build prompt ----------
    # Supabase calls are synchronous, so run them in a worker thread.
    restaurant_info, greeting = await asyncio.to_thread(
        build_restaurant_info,
        RESTAURANT_ID,
    )

    system_prompt = get_voice_prompt(restaurant_info)

    # LLMContext expects a list of message dictionaries.
    messages = [
        {
            "role": "system",
            "content": system_prompt,
        }
    ]

    context = LLMContext(messages, tools=TOOLS)

    # ---------- Speech to text ----------
    stt = SarvamRealtimeSTTService(
        api_key=os.environ["SARVAM_API_KEY"],
        sample_rate=16000,
        settings=SarvamRealtimeSTTService.Settings(
            language_code="en-IN",
            mode="transcribe",
            stream_type="fast",
        ),
        should_interrupt=True,
    )

    # ---------- LLM ----------
    llm = GroqLLMService(
        api_key=os.environ["GROQ_API_KEY"],
        settings=GroqLLMService.Settings(
            model=os.environ("GROQ_MODEL","openai/gpt-oss-120b"),
        ),
    )

    # ---------- Text to speech ----------
    tts = SarvamTTSService(
        api_key=os.environ["SARVAM_API_KEY"],
        settings=SarvamTTSService.Settings(
            model=os.environ["TTS_MODEL","bulbul:v3"],
            voice=os.environ["VOICE","shubh"],
            language=os.environ["LANGUAGE","en-IN"],
        ),
        sample_rate=24000,
    )

    # ---------- Conversation context ----------
    user_aggregator, assistant_aggregator = (
        LLMContextAggregatorPair(
            context,
            user_params=LLMUserAggregatorParams(
                user_turn_strategies=UserTurnStrategies(
                    start=[ExternalUserTurnStartStrategy()],
                    stop=[
                        ExternalUserTurnStopStrategy(
                            timeout=0.2,
                            wait_for_transcript=True,
                        )
                    ],
                ),
                user_turn_stop_timeout=3.0,
            ),
        )
    )

    # ---------- Pipeline ----------
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
        params=PipelineParams(enable_metrics=True),
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Caller connected")

        messages.append({
            "role": "assistant",
            "content": greeting,
        })

        await worker.queue_frame(TTSSpeakFrame(greeting))

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Caller disconnected")
        await worker.cancel()

    runner = WorkerRunner()
    await runner.add_workers(worker)
    await runner.run()


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()