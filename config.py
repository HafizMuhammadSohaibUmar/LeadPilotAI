"""Application configuration via pydantic-settings.

Every value is scoped per deployment; BUSINESS_ID makes the whole stack
multi-tenant from day one (all DB rows and config lookups carry it).
"""
from functools import lru_cache
from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Multi-tenancy ---
    business_id: str = "default-business"
    business_name: str = "Acme Home Services"

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8000
    public_base_url: str = "http://localhost:8000"  # ngrok / prod URL for Twilio callbacks
    log_level: str = "INFO"

    # --- Twilio ---
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""
    owner_phone_number: str = ""  # receives emergency SMS + lead summaries
    validate_twilio_signature: bool = True
    enable_recording: bool = False  # per-deployment toggle

    # --- Deepgram ---
    deepgram_api_key: str = ""
    deepgram_model: str = "nova-2"

    # --- ElevenLabs ---
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"
    elevenlabs_model: str = "eleven_flash_v2_5"  # lowest-latency tier
    use_elevenlabs_tts: bool = True  # False -> fall back to Twilio <Say> ($0)

    # --- LLM (LiteLLM only) ---
    groq_api_key: str = ""
    mistral_api_key: str = ""
    llm_primary: str = "groq/llama-3.3-70b-versatile"
    llm_fallback: str = "mistral/mistral-small-latest"
    llm_tertiary: str = "mistral/open-ministral-3b"
    llm_timeout_seconds: float = 3.0
    # Groq free tier for llama-3.3-70b-versatile: 30 req/min, 1000 req/day.
    groq_daily_request_cap: int = 1000
    groq_warn_threshold: float = 0.8  # warn at 80% of daily cap

    # --- Supabase ---
    supabase_url: str = ""
    supabase_key: str = ""

    # --- FSM ---
    fsm_provider: str = "generic"  # jobber | housecallpro | generic
    jobber_api_token: str = ""
    jobber_graphql_url: str = "https://api.getjobber.com/api/graphql"
    housecallpro_api_key: str = ""
    housecallpro_base_url: str = "https://api.housecallpro.com"

    # --- Service area ---
    service_area_zip_codes: str = "78701,78702,78703,78704,78705"
    referral_business_name: str = ""  # optional referral offered on polite decline
    referral_business_phone: str = ""

    # --- Conversation timeouts ---
    silence_prompt_seconds: float = 8.0   # prompt caller once after 8s silence
    silence_hangup_seconds: float = 5.0   # end call 5s after the prompt

    # --- Duplicate detection ---
    duplicate_window_minutes: int = 60

    @property
    def zip_list(self) -> List[str]:
        return [z.strip() for z in self.service_area_zip_codes.split(",") if z.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
