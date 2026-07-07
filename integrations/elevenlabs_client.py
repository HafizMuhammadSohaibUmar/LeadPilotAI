"""ElevenLabs Flash TTS -> mulaw/8kHz audio for Twilio Media Streams.

Uses the Flash model (lowest-latency tier) and requests `ulaw_8000` output
directly so no transcoding is needed before piping into Twilio.

If USE_ELEVENLABS_TTS=false (credits ran out, or a $0 deployment), callers of
`synthesize()` receive None and main.py falls back to Twilio's built-in <Say>
TTS via the /say redirect — the conversation logic is unchanged.
"""
import logging
from typing import Optional

import httpx

from config import get_settings
from middleware import Timer, log_event

logger = logging.getLogger("elevenlabs")


class ElevenLabsClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.elevenlabs_api_key
        self.voice_id = settings.elevenlabs_voice_id
        self.model = settings.elevenlabs_model
        self.enabled = settings.use_elevenlabs_tts

    async def synthesize(self, text: str,
                         call_id: Optional[str] = None) -> Optional[bytes]:
        """Return raw mulaw/8kHz bytes, or None if disabled/failed.

        Returning None (instead of raising) lets the voice loop transparently
        degrade to Twilio <Say> without try/except at every call site.
        """
        if not self.enabled or not text:
            return None
        url = (
            f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}"
            "?output_format=ulaw_8000"
        )
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                with Timer() as timer:
                    resp = await client.post(
                        url,
                        headers={"xi-api-key": self.api_key},
                        json={
                            "text": text,
                            "model_id": self.model,
                            # Flash-optimized settings for phone audio.
                            "voice_settings": {
                                "stability": 0.5,
                                "similarity_boost": 0.75,
                            },
                        },
                    )
                resp.raise_for_status()
            log_event(logger, "TTS synthesized", call_id=call_id,
                      action="tts_synthesized", latency_ms=timer.latency_ms,
                      text_length=len(text))
            return resp.content
        except Exception as exc:
            log_event(logger, f"TTS failed, will fall back to <Say>: {exc}",
                      call_id=call_id, action="tts_failed",
                      level=logging.WARNING)
            return None


elevenlabs_client = ElevenLabsClient()
