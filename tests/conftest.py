"""Shared fixtures. Env vars are pinned BEFORE app modules import so
get_settings() (lru_cached) sees deterministic test values."""
import os

# Must happen before any project import.
os.environ.update({
    "BUSINESS_ID": "test-business",
    "BUSINESS_NAME": "Test HVAC Co",
    "PUBLIC_BASE_URL": "http://testserver",
    "TWILIO_ACCOUNT_SID": "ACtest",
    "TWILIO_AUTH_TOKEN": "test_auth_token",
    "TWILIO_PHONE_NUMBER": "+15550001111",
    "OWNER_PHONE_NUMBER": "+15550002222",
    "VALIDATE_TWILIO_SIGNATURE": "true",
    "DEEPGRAM_API_KEY": "dg_test",
    "ELEVENLABS_API_KEY": "el_test",
    "USE_ELEVENLABS_TTS": "false",
    "GROQ_API_KEY": "gsk_test",
    "MISTRAL_API_KEY": "mistral_test",
    "LLM_TERTIARY": "mistral/ministral-3b-latest",
    "SUPABASE_URL": "https://test.supabase.co",
    "SUPABASE_KEY": "sb_test",
    "FSM_PROVIDER": "generic",
    "SERVICE_AREA_ZIP_CODES": "78701,78702,78703",
    "ENABLE_RECORDING": "false",
})

import pytest
from twilio.request_validator import RequestValidator


@pytest.fixture
def twilio_signature():
    """Compute a REAL valid X-Twilio-Signature for the given URL + form,
    exactly like Twilio does, so the happy path exercises actual validation."""
    validator = RequestValidator(os.environ["TWILIO_AUTH_TOKEN"])

    def _sign(url: str, params: dict) -> str:
        return validator.compute_signature(url, params)

    return _sign


@pytest.fixture
def anyio_backend():
    return "asyncio"
