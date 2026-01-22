#
# Copyright (c) 2024-2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Kyutai Pocket TTS service implementation."""

from typing import AsyncGenerator, Optional

import aiohttp
from loguru import logger

from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.services.tts_service import TTSService
from pipecat.utils.tracing.service_decorators import traced_tts


class PocketTTSService(TTSService):
    """Kyutai Pocket TTS service implementation.

    Interacts with the pocket-tts HTTP server.
    """

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:8000",
        voice_id: Optional[str] = None,
        sample_rate: int = 24000,
        **kwargs,
    ):
        super().__init__(sample_rate=sample_rate, **kwargs)
        self._base_url = base_url.rstrip("/")
        self._voice_id = voice_id
        self._session: Optional[aiohttp.ClientSession] = None

    def can_generate_metrics(self) -> bool:
        return True

    async def start(self, frame_provider):
        await super().start(frame_provider)
        self._session = aiohttp.ClientSession()

    async def stop(self):
        if self._session:
            await self._session.close()
            self._session = None
        await super().stop()

    @traced_tts
    async def run_tts(self, text: str) -> AsyncGenerator[Frame, None]:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession()

        logger.debug(f"Generating TTS with Pocket TTS: [{text}]")
        
        # Use /tts endpoint with multipart/form-data
        url = f"{self._base_url}/tts"
        data = aiohttp.FormData()
        data.add_field("text", text)

        try:
            await self.start_ttfb_metrics()
            async with self._session.post(url, data=data) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Pocket TTS error: {response.status} - {error_text}")
                    yield ErrorFrame(f"Pocket TTS error: {response.status}")
                    return

                await self.start_tts_usage_metrics(text)
                yield TTSStartedFrame()

                # Pocket TTS returns a WAV file. We need to strip the header.
                async for frame in self._stream_audio_frames_from_iterator(
                    response.content.iter_chunked(self.chunk_size),
                    strip_wav_header=True
                ):
                    await self.stop_ttfb_metrics()
                    yield frame

        except Exception as e:
            logger.error(f"Pocket TTS exception: {e}")
            yield ErrorFrame(f"Pocket TTS exception: {e}")
        finally:
            await self.stop_ttfb_metrics()
            yield TTSStoppedFrame()
