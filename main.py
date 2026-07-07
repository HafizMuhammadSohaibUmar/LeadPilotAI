"""Inbound AI voice agent for home service businesses.

FastAPI entrypoint:
    GET  /             — branded static landing page for humans
  POST /voice          — Twilio inbound-call webhook (returns Media Stream TwiML)
  WS   /media-stream/{call_sid} — bidirectional Twilio <-> Deepgram/LLM/TTS loop
  POST /call-status    — Twilio call-status callback (final duration bookkeeping)
  GET  /health         — LLM (all 3 tiers) + DB + Twilio reachability & latency
"""
import asyncio
import base64
from html import escape
from xml.sax.saxutils import escape as xml_escape
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Optional

from fastapi import Depends, FastAPI, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from config import get_settings
from middleware import Timer, log_event, setup_logging, validate_twilio_request
from agent import prompts
from agent.graph import agent_graph
from agent.state import CallState
from integrations.deepgram_client import DeepgramTranscriber
from integrations.elevenlabs_client import elevenlabs_client
from integrations.llm_client import AllProvidersFailedError, llm_client
from integrations.supabase_client import supabase_client
from integrations.twilio_client import twilio_client
from models.call import CallOutcome, CallRecord

logger = logging.getLogger("main")
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DECISIONS_PATH = BASE_DIR / "DECISIONS.md"

# Live call registry: call_sid -> CallSession (single-worker deployment).
active_sessions: Dict[str, "CallSession"] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    log_event(logger, "Voice agent starting", action="startup")
    yield
    log_event(logger, "Voice agent stopping", action="shutdown")


app = FastAPI(title="Home Services Voice Agent", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _normalize_phone_for_tel(phone_number: str) -> str:
    return "".join(ch for ch in phone_number if ch.isdigit() or ch == "+")


def _render_landing_page() -> str:
    settings = get_settings()
    template = (STATIC_DIR / "landing.html").read_text(encoding="utf-8")
    phone_number = settings.twilio_phone_number.strip() or "Set TWILIO_PHONE_NUMBER"
    phone_link = _normalize_phone_for_tel(phone_number)
    substitutions = {
        "BUSINESS_NAME": escape(settings.business_name),
        "PHONE_NUMBER": escape(phone_number),
        "PHONE_LINK": escape(phone_link, quote=True),
        "PUBLIC_BASE_URL": escape(settings.public_base_url, quote=True),
        "GITHUB_URL": "https://github.com/HafizMuhammadSohaibUmar/LeadPilotAI",
        "DECISIONS_URL": "/DECISIONS.md",
        "CURRENT_YEAR": str(time.gmtime().tm_year),
    }
    for key, value in substitutions.items():
        template = template.replace(f"{{{{{key}}}}}", value)
    return template


@app.get("/", include_in_schema=False)
async def landing_page():
    return HTMLResponse(_render_landing_page())


@app.get("/DECISIONS.md", include_in_schema=False)
async def decisions_md():
    return PlainTextResponse(DECISIONS_PATH.read_text(encoding="utf-8"), media_type="text/markdown")


# ---------------------------------------------------------------------------
# Call session — owns per-call state and the silence watchdog
# ---------------------------------------------------------------------------
class CallSession:
    def __init__(self, call_sid: str, caller_number: str) -> None:
        settings = get_settings()
        self.call_sid = call_sid
        self.caller_number = caller_number
        self.started_at = time.time()
        self.stream_sid: Optional[str] = None
        self.websocket: Optional[WebSocket] = None
        self.transcriber: Optional[DeepgramTranscriber] = None
        self.silence_prompted = False
        self.last_activity = time.time()
        self.processing = False  # guard against overlapping turns
        self.reconnecting_for_say = False
        self.state: CallState = {
            "business_id": settings.business_id,
            "call_id": call_sid,
            "caller_id": caller_number,
            "messages": [],
            "current_node": "greeting_node",
            "user_input": "",
            "is_emergency_intercept": False,
            "conversation_complete": False,
        }

    def touch(self) -> None:
        self.last_activity = time.time()
        self.silence_prompted = False


# ---------------------------------------------------------------------------
# Webhook: inbound call
# ---------------------------------------------------------------------------
@app.post("/voice")
async def voice_webhook(form: dict = Depends(validate_twilio_request)):
    settings = get_settings()
    call_sid = form.get("CallSid", "")
    caller_number = form.get("From", "unknown")

    session = CallSession(call_sid, caller_number)
    active_sessions[call_sid] = session

    # Create the call row up-front so every later node can PATCH it.
    try:
        await supabase_client.insert_call(CallRecord(
            id=call_sid, business_id=settings.business_id,
            caller_number=caller_number,
        ))
    except Exception as exc:
        # DB being down must not block answering the phone.
        log_event(logger, f"Call insert failed: {exc}", call_id=call_sid,
                  action="call_insert_failed", level=logging.ERROR)

    log_event(logger, "Inbound call received", call_id=call_sid,
              action="call_received", caller=caller_number)
    return Response(content=twilio_client.stream_twiml(call_sid),
                    media_type="application/xml")


# ---------------------------------------------------------------------------
# Webhook: call status (fires when the call ends)
# ---------------------------------------------------------------------------
@app.post("/call-status")
async def call_status_webhook(form: dict = Depends(validate_twilio_request)):
    call_sid = form.get("CallSid", "")
    status = form.get("CallStatus", "")
    duration = int(form.get("CallDuration", 0) or 0)

    if status in ("completed", "failed", "busy", "no-answer"):
        session = active_sessions.pop(call_sid, None)
        try:
            await supabase_client.update_call(call_sid, {
                "duration_seconds": duration,
            })
        except Exception as exc:
            log_event(logger, f"Duration update failed: {exc}",
                      call_id=call_sid, action="status_update_failed",
                      level=logging.ERROR)
        log_event(logger, f"Call ended: {status}", call_id=call_sid,
                  action="call_ended", duration_seconds=duration)
    return Response(content="<Response/>", media_type="application/xml")


# ---------------------------------------------------------------------------
# WebSocket: Twilio Media Stream <-> STT/LLM/TTS loop
# ---------------------------------------------------------------------------
@app.websocket("/media-stream/{call_sid}")
async def media_stream(websocket: WebSocket, call_sid: str):
    await websocket.accept()
    session = active_sessions.get(call_sid)
    if session is None:
        # Stream for an unknown call (e.g., server restarted mid-call).
        await websocket.close()
        return
    session.websocket = websocket

    turn_queue: asyncio.Queue = asyncio.Queue()

    async def on_transcript(text: str) -> None:
        session.touch()
        await turn_queue.put(text)

    session.transcriber = DeepgramTranscriber(on_transcript, call_id=call_sid)
    try:
        await session.transcriber.connect()
    except Exception as exc:
        log_event(logger, f"Deepgram connect failed: {exc}", call_id=call_sid,
                  action="stt_connect_failed", level=logging.ERROR)
        await _degrade_to_voicemail(session)
        return

    turn_task = asyncio.create_task(_turn_loop(session, turn_queue))
    watchdog_task = asyncio.create_task(_silence_watchdog(session))

    try:
        while True:
            raw = await websocket.receive_text()
            event = json.loads(raw)
            kind = event.get("event")
            if kind == "start":
                session.stream_sid = event["start"]["streamSid"]
                if session.reconnecting_for_say:
                    session.reconnecting_for_say = False
                    session.touch()
                else:
                    # Speak the greeting as soon as the first stream opens.
                    await turn_queue.put("__CALL_START__")
            elif kind == "media":
                audio = base64.b64decode(event["media"]["payload"])
                await session.transcriber.send_audio(audio)
            elif kind == "stop":
                break
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log_event(logger, f"Media stream error: {exc}", call_id=call_sid,
                  action="stream_error", level=logging.ERROR)
    finally:
        turn_task.cancel()
        watchdog_task.cancel()
        await session.transcriber.close()
        if not session.reconnecting_for_say:
            await _finalize_if_incomplete(session)


async def _turn_loop(session: CallSession, queue: asyncio.Queue) -> None:
    """Consume finalized utterances and run one graph turn per utterance."""
    while not session.state.get("conversation_complete"):
        text = await queue.get()
        # Drop utterances that arrive while a turn is mid-flight (simple
        # anti-barge-in; production could buffer and concatenate instead).
        if session.processing:
            continue
        session.processing = True
        try:
            await _run_turn(session, "" if text == "__CALL_START__" else text)
        finally:
            session.processing = False


async def _run_turn(session: CallSession, user_input: str) -> None:
    """One conversational turn: graph invoke -> TTS -> stream to Twilio."""
    session.state["user_input"] = user_input
    with Timer() as timer:
        try:
            result = await agent_graph.ainvoke(session.state)
        except AllProvidersFailedError:
            # PRODUCTION FEATURE 1: entire LLM chain down -> voicemail.
            await _degrade_to_voicemail(session)
            return
        except Exception as exc:
            log_event(logger, f"Graph turn failed: {exc}",
                      call_id=session.call_sid, action="turn_error",
                      level=logging.ERROR)
            await _speak(session, prompts.CLARIFICATION_FALLBACK)
            return
    session.state.update(result)
    log_event(logger, "Turn completed", call_id=session.call_sid,
              node=session.state.get("current_node"), action="turn_completed",
              latency_ms=timer.latency_ms,
              llm_provider_used=session.state.get("llm_provider_used"))

    response_text = session.state.get("agent_response", "")
    if response_text:
        await _speak(session, response_text)
    session.touch()

    if session.state.get("conversation_complete"):
        # Give Twilio a moment to flush the goodbye audio, then hang up.
        await asyncio.sleep(3)
        await _hangup(session)


async def _speak(session: CallSession, text: str) -> None:
    """TTS the text and stream it into the call; fall back to Twilio <Say>."""
    audio = await elevenlabs_client.synthesize(text, call_id=session.call_sid)
    if audio is not None and session.websocket and session.stream_sid:
        await session.websocket.send_text(json.dumps({
            "event": "media",
            "streamSid": session.stream_sid,
            "media": {"payload": base64.b64encode(audio).decode()},
        }))
        return
    # $0 fallback: redirect the live call to <Say> TwiML, then reconnect the
    # stream. Uses Twilio's call-update REST endpoint.
    await _redirect_to_say(session, text)


async def _redirect_to_say(session: CallSession, text: str) -> None:
    """Update the live call with <Say> TwiML + re-<Connect> the stream."""
    import httpx
    settings = get_settings()
    twiml = (
        f"<Response><Say voice=\"Polly.Joanna\">{xml_escape(text)}</Say>"
        f"<Connect><Stream url=\""
        f"{settings.public_base_url.replace('https://', 'wss://').replace('http://', 'ws://')}"
        f"/media-stream/{session.call_sid}\"/></Connect></Response>"
    )
    try:
        session.reconnecting_for_say = True
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{twilio_client.api_base}/Calls/{session.call_sid}.json",
                auth=(twilio_client.account_sid, twilio_client.auth_token),
                data={"Twiml": twiml},
            )
            resp.raise_for_status()
    except Exception as exc:
        session.reconnecting_for_say = False
        log_event(logger, f"<Say> fallback failed: {exc}",
                  call_id=session.call_sid, action="say_fallback_failed",
                  level=logging.ERROR)


async def _silence_watchdog(session: CallSession) -> None:
    """PRODUCTION FEATURE 2: prompt at 8s of silence, hang up 5s later."""
    settings = get_settings()
    while not session.state.get("conversation_complete"):
        await asyncio.sleep(1)
        if session.processing:
            continue
        idle = time.time() - session.last_activity
        if not session.silence_prompted and idle >= settings.silence_prompt_seconds:
            session.silence_prompted = True
            session.last_activity = time.time()  # restart clock for hangup leg
            log_event(logger, "Silence prompt", call_id=session.call_sid,
                      action="silence_prompt")
            await _speak(session, prompts.SILENCE_PROMPT)
        elif session.silence_prompted and idle >= settings.silence_hangup_seconds:
            log_event(logger, "Silence hangup", call_id=session.call_sid,
                      action="silence_hangup")
            session.state["call_outcome"] = CallOutcome.ABANDONED
            await _speak(session, prompts.SILENCE_GOODBYE)
            await asyncio.sleep(2)
            await _hangup(session)
            break


async def _hangup(session: CallSession) -> None:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{twilio_client.api_base}/Calls/{session.call_sid}.json",
                auth=(twilio_client.account_sid, twilio_client.auth_token),
                data={"Status": "completed"},
            )
    except Exception as exc:
        log_event(logger, f"Hangup failed: {exc}", call_id=session.call_sid,
                  action="hangup_failed", level=logging.ERROR)


async def _degrade_to_voicemail(session: CallSession) -> None:
    """PRODUCTION FEATURE 1: whole LLM chain (or STT) is down -> voicemail."""
    import httpx
    log_event(logger, "Degrading to voicemail", call_id=session.call_sid,
              action="voicemail_degradation", level=logging.WARNING)
    session.state["call_outcome"] = CallOutcome.VOICEMAIL
    session.state["conversation_complete"] = True
    twiml = twilio_client.voicemail_twiml(prompts.VOICEMAIL_FALLBACK)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{twilio_client.api_base}/Calls/{session.call_sid}.json",
                auth=(twilio_client.account_sid, twilio_client.auth_token),
                data={"Twiml": twiml},
            )
    except Exception as exc:
        log_event(logger, f"Voicemail redirect failed: {exc}",
                  call_id=session.call_sid, action="voicemail_failed",
                  level=logging.ERROR)
    await _finalize_if_incomplete(session)


async def _finalize_if_incomplete(session: CallSession) -> None:
    """Persist the call summary even if the caller hung up mid-flow."""
    outcome = session.state.get("call_outcome")
    from agent.nodes import call_summary_node
    if session.state.get("current_node") != "call_summary_done":
        if outcome is None:
            session.state["call_outcome"] = CallOutcome.ABANDONED
        try:
            await call_summary_node(session.state)
        except Exception as exc:
            log_event(logger, f"Finalize failed: {exc}",
                      call_id=session.call_sid, action="finalize_failed",
                      level=logging.ERROR)
        session.state["current_node"] = "call_summary_done"


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    """PRODUCTION FEATURE 5: check all 3 LLM tiers, DB, Twilio, with latency."""
    with Timer() as timer:
        llm_results, db_result, twilio_result = await asyncio.gather(
            llm_client.health_check(),
            supabase_client.health_check(),
            twilio_client.health_check(),
        )
    all_ok = (
        any(r.get("ok") for r in llm_results.values())  # at least one tier up
        and db_result.get("ok", False)
        and twilio_result.get("ok", False)
    )
    return {
        "status": "healthy" if all_ok else "degraded",
        "business_id": get_settings().business_id,
        "latency_ms": timer.latency_ms,
        "llm": llm_results,
        "database": db_result,
        "twilio": twilio_result,
        "groq_daily_requests": llm_client.groq_counter.count,
    }


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run("main:app", host=settings.host, port=settings.port)
