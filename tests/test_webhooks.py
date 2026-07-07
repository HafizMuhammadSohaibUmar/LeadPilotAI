"""Integration tests for the Twilio webhooks + /health.

External services (Supabase, Twilio REST, LiteLLM) are mocked; the Twilio
signature check runs FOR REAL using signatures computed the same way Twilio
computes them — happy path proves valid signatures pass, failure path proves
forged requests are rejected with 403.
"""
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from config import Settings
import main
from main import app

client = TestClient(app)

ROOT_URL = "http://testserver/"
VOICE_URL = "http://testserver/voice"
STATUS_URL = "http://testserver/call-status"


# ---------------------------------------------------------------------------
# /
# ---------------------------------------------------------------------------
def test_root_landing_page_uses_branding_and_links():
    settings = Settings(
        business_name="LeadPilot AI",
        twilio_phone_number="+15551234567",
        public_base_url="https://leadpilotai.sohaib.systems",
    )
    with patch.object(main, "get_settings", return_value=settings):
        resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "LeadPilot AI" in resp.text
    assert "+15551234567" in resp.text
    assert "/static/architecture.svg" in resp.text
    assert "https://github.com/HafizMuhammadSohaibUmar/LeadPilotAI" in resp.text
    assert "/DECISIONS.md" in resp.text

    decisions = client.get("/DECISIONS.md")
    assert decisions.status_code == 200
    assert "LeadPilot AI Decisions" in decisions.text


# ---------------------------------------------------------------------------
# /voice
# ---------------------------------------------------------------------------
def test_voice_webhook_happy_path(twilio_signature):
    form = {"CallSid": "CA123", "From": "+15559998888"}
    with patch.object(main.supabase_client, "insert_call",
                      new=AsyncMock(return_value={"id": "CA123"})) as mock_insert:
        resp = client.post(
            "/voice", data=form,
            headers={"X-Twilio-Signature": twilio_signature(VOICE_URL, form)},
        )
    assert resp.status_code == 200
    assert "application/xml" in resp.headers["content-type"]
    # TwiML must open the media stream back to us.
    assert "<Connect>" in resp.text and "media-stream/CA123" in resp.text
    mock_insert.assert_awaited_once()
    assert "CA123" in main.active_sessions
    main.active_sessions.clear()


def test_voice_webhook_rejects_invalid_signature():
    form = {"CallSid": "CA999", "From": "+15550000000"}
    resp = client.post("/voice", data=form,
                       headers={"X-Twilio-Signature": "forged-signature"})
    assert resp.status_code == 403
    assert "CA999" not in main.active_sessions


def test_voice_webhook_survives_db_outage(twilio_signature):
    """DB down must NOT prevent answering the phone (graceful degradation)."""
    form = {"CallSid": "CA456", "From": "+15551112222"}
    with patch.object(main.supabase_client, "insert_call",
                      new=AsyncMock(side_effect=RuntimeError("db down"))):
        resp = client.post(
            "/voice", data=form,
            headers={"X-Twilio-Signature": twilio_signature(VOICE_URL, form)},
        )
    assert resp.status_code == 200
    assert "<Connect>" in resp.text
    main.active_sessions.clear()


# ---------------------------------------------------------------------------
# /call-status
# ---------------------------------------------------------------------------
def test_call_status_happy_path(twilio_signature):
    form = {"CallSid": "CA123", "CallStatus": "completed", "CallDuration": "42"}
    with patch.object(main.supabase_client, "update_call",
                      new=AsyncMock(return_value={})) as mock_update:
        resp = client.post(
            "/call-status", data=form,
            headers={"X-Twilio-Signature": twilio_signature(STATUS_URL, form)},
        )
    assert resp.status_code == 200
    mock_update.assert_awaited_once_with("CA123", {"duration_seconds": 42})


def test_call_status_rejects_invalid_signature():
    form = {"CallSid": "CA123", "CallStatus": "completed"}
    with patch.object(main.supabase_client, "update_call",
                      new=AsyncMock()) as mock_update:
        resp = client.post("/call-status", data=form,
                           headers={"X-Twilio-Signature": "bad"})
    assert resp.status_code == 403
    mock_update.assert_not_awaited()


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------
def test_health_all_up():
    llm_ok = {
        "groq/llama-3.3-70b-versatile": {"ok": True, "latency_ms": 100},
        "mistral/mistral-small-latest": {"ok": True, "latency_ms": 150},
        "mistral/ministral-3b-latest": {"ok": True, "latency_ms": 120},
    }
    with patch.object(main.llm_client, "health_check",
                      new=AsyncMock(return_value=llm_ok)), \
         patch.object(main.supabase_client, "health_check",
                      new=AsyncMock(return_value={"ok": True, "latency_ms": 30})), \
         patch.object(main.twilio_client, "health_check",
                      new=AsyncMock(return_value={"ok": True, "latency_ms": 50})):
        resp = client.get("/health")
    body = resp.json()
    assert resp.status_code == 200
    assert body["status"] == "healthy"
    assert "latency_ms" in body
    assert len(body["llm"]) == 3  # all three tiers checked


def test_health_degraded_when_db_down():
    llm_ok = {"groq/llama-3.3-70b-versatile": {"ok": True, "latency_ms": 100}}
    with patch.object(main.llm_client, "health_check",
                      new=AsyncMock(return_value=llm_ok)), \
         patch.object(main.supabase_client, "health_check",
                      new=AsyncMock(return_value={"ok": False, "error": "timeout"})), \
         patch.object(main.twilio_client, "health_check",
                      new=AsyncMock(return_value={"ok": True, "latency_ms": 50})):
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"
