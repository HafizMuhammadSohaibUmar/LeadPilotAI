"""Tests for the LiteLLM 3-tier fallback chain and the Groq daily counter.

litellm.acompletion is mocked — no network. Proves the Mistral fallback path
is real and tested, not theoretical.
"""
import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from integrations.llm_client import (
    AllProvidersFailedError,
    GroqUsageCounter,
    LLMClient,
)

PRIMARY = "groq/llama-3.3-70b-versatile"
FALLBACK = "mistral/mistral-small-latest"
TERTIARY = "mistral/ministral-3b-latest"

MESSAGES = [{"role": "user", "content": "classify this"}]


def _response(text: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


class RateLimitError(Exception):
    """Stand-in for a provider HTTP 429."""


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_primary_serves_and_is_reported():
    client = LLMClient()
    with patch("integrations.llm_client.litellm.acompletion",
               new=AsyncMock(return_value=_response("hvac_repair"))) as mock:
        text, provider = await client.complete(MESSAGES)
    assert text == "hvac_repair"
    assert provider == PRIMARY  # llm_provider_used field
    assert mock.await_args.kwargs["model"] == PRIMARY
    assert client.groq_counter.count == 1  # Groq usage counted


# ---------------------------------------------------------------------------
# Fallback on 429 / errors
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fallback_to_mistral_on_429():
    client = LLMClient()

    async def flaky(model, **kwargs):
        if model == PRIMARY:
            raise RateLimitError("429 rate limit")
        return _response("SAME_DAY")

    with patch("integrations.llm_client.litellm.acompletion", new=flaky):
        text, provider = await client.complete(MESSAGES)
    assert text == "SAME_DAY"
    assert provider == FALLBACK
    assert client.groq_counter.count == 0  # failed Groq call not counted


@pytest.mark.asyncio
async def test_tertiary_when_primary_and_fallback_fail():
    client = LLMClient()

    async def very_flaky(model, **kwargs):
        if model in (PRIMARY, FALLBACK):
            raise RateLimitError("503 unavailable")
        return _response("ok")

    with patch("integrations.llm_client.litellm.acompletion", new=very_flaky):
        text, provider = await client.complete(MESSAGES)
    assert provider == TERTIARY


# ---------------------------------------------------------------------------
# Fallback on timeout (>3s)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fallback_on_timeout():
    client = LLMClient()
    client.timeout = 0.05  # shrink for test speed; behavior identical

    async def slow_then_fast(model, **kwargs):
        if model == PRIMARY:
            await asyncio.sleep(1)  # exceeds timeout -> asyncio.TimeoutError
        return _response("SCHEDULED")

    with patch("integrations.llm_client.litellm.acompletion",
               new=slow_then_fast):
        text, provider = await client.complete(MESSAGES)
    assert provider == FALLBACK


# ---------------------------------------------------------------------------
# Failure path: whole chain exhausted
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_all_providers_failed_raises():
    client = LLMClient()
    with patch("integrations.llm_client.litellm.acompletion",
               new=AsyncMock(side_effect=RateLimitError("down"))):
        with pytest.raises(AllProvidersFailedError):
            await client.complete(MESSAGES)


# ---------------------------------------------------------------------------
# Groq daily usage counter (1000/day free tier, warn at 80%)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_groq_counter_warns_at_80_percent(caplog):
    counter = GroqUsageCounter(daily_cap=10, warn_threshold=0.8)
    with caplog.at_level(logging.WARNING, logger="llm"):
        for _ in range(7):
            await counter.increment()
        assert not any("free-tier cap" in r.getMessage() for r in caplog.records)
        await counter.increment()  # 8th request == 80% of 10
    assert any("free-tier cap" in r.getMessage() for r in caplog.records)
    # Warning fires exactly once, not on every request past the threshold.
    warnings = [r for r in caplog.records if "free-tier cap" in r.getMessage()]
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_groq_skipped_when_daily_cap_exhausted():
    client = LLMClient()
    client.groq_counter = GroqUsageCounter(daily_cap=1, warn_threshold=0.8)
    await client.groq_counter.increment()  # cap now spent
    assert client.groq_counter.exhausted

    called_models = []

    async def record(model, **kwargs):
        called_models.append(model)
        return _response("ok")

    with patch("integrations.llm_client.litellm.acompletion", new=record):
        text, provider = await client.complete(MESSAGES)
    assert PRIMARY not in called_models  # Groq tier skipped entirely
    assert provider == FALLBACK
