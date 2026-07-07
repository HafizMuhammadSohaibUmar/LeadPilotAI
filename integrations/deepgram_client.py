"""Deepgram streaming STT over raw WebSocket.

We speak the Deepgram live-transcription WebSocket protocol directly with the
`websockets` library instead of the Deepgram SDK: fewer dependencies, and we
need precise control over the mulaw/8kHz frames coming from Twilio Media
Streams.

Twilio sends audio as base64 mulaw @ 8000 Hz mono; Deepgram accepts that
natively with encoding=mulaw&sample_rate=8000.
"""
import asyncio
import json
import logging
from typing import Awaitable, Callable, Optional

import websockets

from config import get_settings
from middleware import log_event

logger = logging.getLogger("deepgram")

DEEPGRAM_WS_URL = "wss://api.deepgram.com/v1/listen"


class DeepgramTranscriber:
    """One instance per phone call. Feed it Twilio audio frames; it invokes
    `on_transcript(text)` whenever Deepgram finalizes an utterance."""

    def __init__(
        self,
        on_transcript: Callable[[str], Awaitable[None]],
        call_id: Optional[str] = None,
    ) -> None:
        settings = get_settings()
        self.api_key = settings.deepgram_api_key
        self.model = settings.deepgram_model
        self.on_transcript = on_transcript
        self.call_id = call_id
        self._ws = None
        self._receive_task: Optional[asyncio.Task] = None

    @property
    def _url(self) -> str:
        # endpointing=300: finalize an utterance after 300ms of silence —
        # tuned for snappy phone turn-taking.
        params = (
            f"?model={self.model}"
            "&encoding=mulaw&sample_rate=8000&channels=1"
            "&punctuate=true&interim_results=true&endpointing=300"
            "&smart_format=true"
        )
        return DEEPGRAM_WS_URL + params

    async def connect(self) -> None:
        self._ws = await websockets.connect(
            self._url,
            additional_headers={"Authorization": f"Token {self.api_key}"},
        )
        self._receive_task = asyncio.create_task(self._receive_loop())
        log_event(logger, "Deepgram stream connected", call_id=self.call_id,
                  action="stt_connected")

    async def send_audio(self, mulaw_bytes: bytes) -> None:
        if self._ws is not None:
            await self._ws.send(mulaw_bytes)

    async def _receive_loop(self) -> None:
        try:
            async for raw in self._ws:
                message = json.loads(raw)
                if message.get("type") != "Results":
                    continue
                alt = (message.get("channel", {})
                              .get("alternatives", [{}]))[0]
                transcript = alt.get("transcript", "").strip()
                # Only act on finalized utterances; interim results are
                # useful for barge-in but we keep turn-taking simple.
                if transcript and message.get("is_final"):
                    log_event(logger, "Transcript finalized",
                              call_id=self.call_id, action="stt_final",
                              transcript=transcript)
                    await self.on_transcript(transcript)
        except websockets.ConnectionClosed:
            log_event(logger, "Deepgram stream closed", call_id=self.call_id,
                      action="stt_closed")
        except Exception as exc:
            log_event(logger, f"Deepgram receive error: {exc}",
                      call_id=self.call_id, action="stt_error",
                      level=logging.ERROR)

    async def close(self) -> None:
        if self._ws is not None:
            try:
                # Tell Deepgram we're done so it flushes any pending finals.
                await self._ws.send(json.dumps({"type": "CloseStream"}))
                await self._ws.close()
            except Exception:
                pass
        if self._receive_task:
            self._receive_task.cancel()
