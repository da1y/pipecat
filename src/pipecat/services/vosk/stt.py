#
# Copyright (c) 2024-2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Vosk Speech-to-Text service implementation."""

import asyncio
import json
from typing import AsyncGenerator, Optional

from loguru import logger

from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
)
from pipecat.services.stt_service import STTService
from pipecat.services.websocket_service import WebsocketService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601
from pipecat.utils.tracing.service_decorators import traced_stt


class VoskSTTService(STTService, WebsocketService):
    """Vosk STT service implementation using WebSockets."""

    def __init__(
        self,
        *,
        uri: str = "ws://localhost:2700",
        sample_rate: int = 16000,
        language: Language = Language.EN,
        **kwargs,
    ):
        STTService.__init__(self, **kwargs)
        WebsocketService.__init__(self, **kwargs)
        self._uri = uri
        self._sample_rate = sample_rate
        self._language = language
        self._receive_task = None

    def can_generate_metrics(self) -> bool:
        return True

    async def _connect_websocket(self):
        import websockets
        try:
            logger.debug(f"Connecting to Vosk at {self._uri}")
            self._websocket = await websockets.connect(self._uri)
            # Initialize Vosk with sample rate
            await self._websocket.send(json.dumps({"config": {"sample_rate": self._sample_rate}}))
        except Exception as e:
            logger.error(f"Error connecting to Vosk: {e}")
            raise

    async def _disconnect_websocket(self):
        if self._websocket:
            await self._websocket.close()
            self._websocket = None

    async def _receive_messages(self):
        async for message in self._websocket:
            data = json.loads(message)
            if "text" in data:
                text = data["text"]
                if text:
                    frame = TranscriptionFrame(
                        text=text,
                        user_id=self._user_id,
                        timestamp=time_now_iso8601(),
                        language=self._language,
                    )
                    await self.push_frame(frame)
            elif "partial" in data:
                partial = data["partial"]
                if partial:
                    frame = InterimTranscriptionFrame(
                        text=partial,
                        user_id=self._user_id,
                        timestamp=time_now_iso8601(),
                        language=self._language,
                    )
                    await self.push_frame(frame)

    @traced_stt
    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        if not self._websocket:
            await self._connect()

        try:
            await self.start_processing_metrics()
            await self.start_ttfb_metrics()

            await self._websocket.send(audio)
            
            # We need to yield something to keep the generator alive if needed,
            # but actually STTService.process_frame expects a generator that yields frames.
            # Since we push frames asynchronously in _receive_messages, we can just return.
            if False:
                yield
        except Exception as e:
            logger.error(f"Vosk STT error: {e}")
            yield ErrorFrame(f"Vosk STT error: {e}")
        finally:
            await self.stop_ttfb_metrics()
            await self.stop_processing_metrics()

    async def start(self, frame_provider):
        await super().start(frame_provider)
        # Start the websocket receive task
        self._receive_task = asyncio.create_task(self._receive_task_handler(self.push_error))

    def push_error(self, error_frame):
        # Helper to push error frames from the receive task
        asyncio.create_task(self.push_frame(error_frame))

    async def stop(self):
        if self._receive_task:
            self._receive_task.cancel()
        await self._disconnect()
        await super().stop()
