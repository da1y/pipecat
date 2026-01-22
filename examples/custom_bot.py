#
# Copyright (c) 2024-2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Custom Pipecat Bot using Local Services.

ASR: Vosk (ws://localhost:2700)
LLM: Liquid LFM 2.5-1.2b (via LMStudio at http://localhost:1234/v1)
TTS: Kyutai Pocket TTS (http://localhost:8000)
"""

import os
import sys

from dotenv import load_dotenv
from loguru import logger

# Add src to path if running from examples
sys.path.append(os.path.join(os.path.dirname(__file__), "../src"))

from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frameworks.rtvi import RTVIObserver, RTVIProcessor
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.kyutai.tts import PocketTTSService
from pipecat.services.vosk.stt import VoskSTTService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.daily.transport import DailyParams
from pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy import (
    TurnAnalyzerUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies

load_dotenv(override=True)

async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    logger.info("Starting local services bot")

    # 1. Vosk ASR
    stt = VoskSTTService(uri="ws://localhost:2700")

    # 2. Kyutai Pocket TTS
    tts = PocketTTSService(base_url="http://localhost:8000")

    # 3. LMStudio LLM (OpenAI compatible)
    llm = OpenAILLMService(
        api_key="not-needed",
        base_url="http://localhost:1234/v1",
        model="liquid/lfm2.5-1.2b"
    )

    messages = [
        {
            "role": "system",
            "content": "You are a friendly AI assistant running locally. Respond naturally and keep your answers conversational.",
        },
    ]

    context = LLMContext(messages)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
    )

    rtvi = RTVIProcessor()

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            rtvi,
            user_aggregator,
            llm,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        observers=[RTVIObserver(rtvi)],
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected")
        messages.append({"role": "system", "content": "Say hello and briefly introduce yourself as a local AI assistant."})
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=runner_args.handle_sigint)
    await runner.run(task)

async def bot(runner_args: RunnerArguments):
    transport_params = {
        "daily": lambda: DailyParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.8)),
        ),
        "webrtc": lambda: TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.8)),
        ),
    }

    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)

if __name__ == "__main__":
    from pipecat.runner.run import main
    main()
