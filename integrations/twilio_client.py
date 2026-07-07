"""Twilio helpers: outbound SMS (async via REST) and TwiML generation.

We call the Twilio REST API directly with httpx instead of the blocking
twilio-python client so SMS sends never stall the voice event loop.
(twilio-python is still a dependency — we use its RequestValidator for
webhook signature checks in middleware.py.)
"""
import logging
from typing import Optional

import httpx
from twilio.twiml.voice_response import Connect, VoiceResponse

from config import get_settings
from middleware import Timer, log_event

logger = logging.getLogger("twilio")


class TwilioClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.account_sid = settings.twilio_account_sid
        self.auth_token = settings.twilio_auth_token
        self.from_number = settings.twilio_phone_number
        self.owner_number = settings.owner_phone_number
        self.api_base = (
            f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}"
        )

    # ------------------------------------------------------------------- SMS
    async def send_sms(self, to: str, body: str,
                       call_id: Optional[str] = None) -> bool:
        """Fire an SMS; returns success flag instead of raising so a failed
        SMS never breaks the live voice conversation."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                with Timer() as timer:
                    resp = await client.post(
                        f"{self.api_base}/Messages.json",
                        auth=(self.account_sid, self.auth_token),
                        data={"To": to, "From": self.from_number, "Body": body},
                    )
                resp.raise_for_status()
            log_event(logger, "SMS sent", call_id=call_id, action="sms_sent",
                      latency_ms=timer.latency_ms, sms_to=to)
            return True
        except Exception as exc:
            log_event(logger, f"SMS send failed: {exc}", call_id=call_id,
                      action="sms_failed", level=logging.ERROR, sms_to=to)
            return False

    async def sms_owner(self, body: str, call_id: Optional[str] = None) -> bool:
        return await self.send_sms(self.owner_number, body, call_id=call_id)

    # ----------------------------------------------------------------- TwiML
    def stream_twiml(self, call_sid: str) -> str:
        """TwiML that opens the bidirectional Media Stream to our WebSocket."""
        settings = get_settings()
        ws_base = settings.public_base_url.replace("https://", "wss://") \
                                          .replace("http://", "ws://")
        response = VoiceResponse()
        if settings.enable_recording:
            # Dual-channel recording of the whole call, per-deployment toggle.
            response.record(record_channels="dual", timeout=0)
        connect = Connect()
        connect.stream(url=f"{ws_base}/media-stream/{call_sid}")
        response.append(connect)
        return str(response)

    def say_twiml(self, text: str, hangup: bool = False) -> str:
        """Plain <Say> TwiML — used for voicemail degradation and as the
        $0 TTS fallback when ElevenLabs is disabled."""
        response = VoiceResponse()
        response.say(text, voice="Polly.Joanna")
        if hangup:
            response.hangup()
        return str(response)

    def voicemail_twiml(self, prompt_text: str) -> str:
        """Graceful degradation: apologize, then record a voicemail."""
        response = VoiceResponse()
        response.say(prompt_text, voice="Polly.Joanna")
        response.record(max_length=120, play_beep=True)
        response.hangup()
        return str(response)

    # ---------------------------------------------------------------- health
    async def health_check(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                with Timer() as timer:
                    resp = await client.get(
                        f"{self.api_base}.json",
                        auth=(self.account_sid, self.auth_token),
                    )
                resp.raise_for_status()
            return {"ok": True, "latency_ms": timer.latency_ms}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


twilio_client = TwilioClient()
