"""LiteLLM-only LLM access layer with a 3-tier automatic fallback chain.

Chain: groq/llama-3.3-70b-versatile -> mistral/mistral-small-latest
       -> mistral/open-ministral-3b

Fallback triggers: timeout (>3s), HTTP 429, or any 5xx/provider error.
Every completion records which provider actually served the turn
(`llm_provider_used`) for structured logging + the calls table.

Groq free tier for llama-3.3-70b-versatile is 30 req/min and 1,000 req/day,
so we keep a daily counter and log a warning at 80% of the cap — this makes
the Mistral fallback path observable in real deployments, not theoretical.
"""
import asyncio
import logging
from datetime import date
from typing import List, Optional, Tuple

import litellm

from config import get_settings
from middleware import Timer, log_event

logger = logging.getLogger("llm")

# Don't let LiteLLM print its own noise into our JSON log stream.
litellm.suppress_debug_info = True


class AllProvidersFailedError(Exception):
    """Raised when the entire fallback chain is exhausted.

    main.py catches this to degrade gracefully to voicemail.
    """


class GroqUsageCounter:
    """In-process daily counter for Groq requests.

    A single Uvicorn worker handles all voice traffic for a deployment, so an
    in-memory counter is accurate enough; it resets at UTC midnight rollover.
    (For multi-worker deployments, move this to Supabase/Redis.)
    """

    def __init__(self, daily_cap: int, warn_threshold: float) -> None:
        self.daily_cap = daily_cap
        self.warn_threshold = warn_threshold
        self._count = 0
        self._day = date.today()
        self._warned = False
        self._lock = asyncio.Lock()

    async def increment(self) -> int:
        async with self._lock:
            today = date.today()
            if today != self._day:  # midnight rollover -> fresh budget
                self._day = today
                self._count = 0
                self._warned = False
            self._count += 1
            if (not self._warned
                    and self._count >= int(self.daily_cap * self.warn_threshold)):
                self._warned = True
                log_event(
                    logger,
                    f"Groq daily usage at {self._count}/{self.daily_cap} "
                    f"({int(self.warn_threshold * 100)}% of free-tier cap) — "
                    "Mistral fallback will absorb overflow",
                    action="groq_usage_warning",
                    level=logging.WARNING,
                    groq_daily_count=self._count,
                )
            return self._count

    @property
    def count(self) -> int:
        return self._count

    @property
    def exhausted(self) -> bool:
        return self._count >= self.daily_cap


class LLMClient:
    """The ONLY gateway to LLMs in this codebase. All calls go through here."""

    def __init__(self) -> None:
        settings = get_settings()
        self.chain: List[str] = [
            settings.llm_primary,
            settings.llm_fallback,
            settings.llm_tertiary,
        ]
        self.timeout = settings.llm_timeout_seconds
        self.groq_counter = GroqUsageCounter(
            settings.groq_daily_request_cap, settings.groq_warn_threshold
        )

    async def complete(
        self,
        messages: List[dict],
        *,
        call_id: Optional[str] = None,
        max_tokens: int = 100,
        temperature: float = 0.3,
    ) -> Tuple[str, str]:
        """Run the completion through the fallback chain.

        Returns (response_text, provider_that_served_it).
        Raises AllProvidersFailedError if all three tiers fail.
        """
        last_error: Optional[Exception] = None

        for model in self.chain:
            # Skip Groq entirely once its daily budget is spent — fail fast to
            # Mistral instead of burning ~3s on a guaranteed 429.
            if model.startswith("groq/") and self.groq_counter.exhausted:
                log_event(logger, "Groq daily cap exhausted, skipping tier",
                          call_id=call_id, action="groq_cap_skip",
                          level=logging.WARNING)
                continue
            try:
                with Timer() as timer:
                    response = await asyncio.wait_for(
                        litellm.acompletion(
                            model=model,
                            messages=messages,
                            max_tokens=max_tokens,
                            temperature=temperature,
                            timeout=self.timeout,
                        ),
                        timeout=self.timeout,
                    )
                if model.startswith("groq/"):
                    await self.groq_counter.increment()
                text = (response.choices[0].message.content or "").strip()
                log_event(logger, "LLM turn served", call_id=call_id,
                          action="llm_completion", latency_ms=timer.latency_ms,
                          llm_provider_used=model)
                return text, model
            except asyncio.TimeoutError as exc:
                last_error = exc
                log_event(logger, f"LLM timeout (> {self.timeout}s) on {model}",
                          call_id=call_id, action="llm_timeout",
                          llm_provider_used=model, level=logging.WARNING)
            except Exception as exc:  # 429 / 5xx / auth / network from LiteLLM
                last_error = exc
                log_event(logger, f"LLM error on {model}: {exc}",
                          call_id=call_id, action="llm_error",
                          llm_provider_used=model, level=logging.WARNING)

        log_event(logger, "All LLM providers failed", call_id=call_id,
                  action="llm_chain_exhausted", level=logging.ERROR)
        raise AllProvidersFailedError(str(last_error))

    async def health_check(self) -> dict:
        """Ping each tier with a 1-token request; used by GET /health."""
        results = {}
        for model in self.chain:
            try:
                with Timer() as timer:
                    await asyncio.wait_for(
                        litellm.acompletion(
                            model=model,
                            messages=[{"role": "user", "content": "ping"}],
                            max_tokens=1,
                            timeout=self.timeout,
                        ),
                        timeout=self.timeout,
                    )
                results[model] = {"ok": True, "latency_ms": timer.latency_ms}
            except Exception as exc:
                results[model] = {"ok": False, "error": str(exc)}
        return results


# Module-level singleton so the graph nodes, webhooks, and /health all share
# the same Groq usage counter.
llm_client = LLMClient()
