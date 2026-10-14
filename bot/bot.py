# bot/bot.py
#
# Browser audio -> Sarvam STT -> Groq LLM + reservation tools
#              -> Sarvam TTS -> Browser audio
#
# Run from the project root:
#   uv run python -m bot.bot

import asyncio
import os
import random
from datetime import datetime, timezone

from dotenv import load_dotenv
from loguru import logger

from pipecat.frames.frames import TTSSpeakFrame
from pipecat.observers.service_metrics_observer import ServiceMetricsObserver
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

from bot.config import RESTAURANT_ID
from bot.filler import DEFAULT_FILLERS, FILLERS
from bot.metrics import SessionMetricsTracker
from bot.prompts import get_voice_prompt
from bot.restaurant_info import build_restaurant_info
from bot.tools import TOOLS
from services.call_record import save_call_record

load_dotenv(override=True)


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

    restaurant_info = await asyncio.to_thread(build_restaurant_info, RESTAURANT_ID)

    # ---------- metrics ----------
    metrics_tracker = SessionMetricsTracker()
    metrics_observer = ServiceMetricsObserver()

    @metrics_observer.event_handler("on_service_latency")
    async def on_service_latency(observer, record):
        metrics_tracker.record_latency(record)

    @metrics_observer.event_handler("on_service_usage")
    async def on_service_usage(observer, record):
        metrics_tracker.record_usage(record)

    # ---------- LLM context ----------
    messages = [{"role": "system", "content": get_voice_prompt(restaurant_info)}]
    context = LLMContext(messages, tools=TOOLS)

    # ---------- services ----------
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
            max_tokens=150,
            temperature=0.3,
        ),
    )

    tts = SarvamTTSService(
    api_key=os.environ["SARVAM_API_KEY"],
    settings=SarvamTTSService.Settings(
        model="bulbul:v3",
        voice="shubh",
        language="en-IN",
        pace=1.0,
        min_buffer_size=30,
        max_chunk_length=150,
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
                        timeout=0.5,               # silence that ends a turn
                        wait_for_transcript=True,  # wait for the final transcript
                    )
                ],
            ),
            user_turn_stop_timeout=1.2,            # fallback max wait
        ),
    )

    # ---------- pipeline ----------
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
            enable_usage_metrics=True,  # LLM tokens & TTS characters
        ),
        observers=[metrics_observer],
    )
    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)

    # ---------- event handlers ----------
    @llm.event_handler("on_function_calls_started")
    async def on_function_calls_started(service, function_calls):
        name = function_calls[0].function_name
        phrase = random.choice(FILLERS.get(name, DEFAULT_FILLERS))
        await worker.queue_frame(TTSSpeakFrame(phrase))

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Caller connected")
        await worker.queue_frame(
            TTSSpeakFrame(
                "Hello! I'm Rosi, your restaurant receptionist. "
                "I can help answer questions, book, cancel, or "
                "reschedule reservations. How can I help you today?"
            )
        )

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        metrics_tracker.print_summary()
        await save_call_record(context.messages, call_start_time, RESTAURANT_ID)
        await worker.cancel()
        await runner.cancel(reason="Caller disconnected")

    await runner.add_workers(worker)
    await runner.run()


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
