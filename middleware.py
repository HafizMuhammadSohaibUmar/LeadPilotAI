"""Structured JSON logging + Twilio webhook signature validation.

Every log line is a single JSON object with call_id / node / action /
latency_ms / llm_provider_used so it can be shipped straight to any
log aggregator (Loki, CloudWatch, Datadog) without parsing rules.
"""
import json
import logging
import sys
import time
from typing import Optional

from fastapi import Request, HTTPException
from twilio.request_validator import RequestValidator

from config import get_settings


# ---------------------------------------------------------------------------
# Structured JSON logging
# ---------------------------------------------------------------------------
class JsonFormatter(logging.Formatter):
    """Render every record as one JSON line; extra fields pass through."""

    RESERVED = {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName", "message",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Merge structured extras (call_id, node, action, latency_ms, ...)
        for key, value in record.__dict__.items():
            if key not in self.RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging() -> None:
    settings = get_settings()
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
    # Silence noisy third-party loggers to keep the JSON stream clean.
    for noisy in ("httpx", "httpcore", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log_event(
    logger: logging.Logger,
    message: str,
    *,
    call_id: Optional[str] = None,
    node: Optional[str] = None,
    action: Optional[str] = None,
    latency_ms: Optional[float] = None,
    llm_provider_used: Optional[str] = None,
    level: int = logging.INFO,
    **extra,
) -> None:
    """Convenience helper enforcing the canonical structured fields."""
    fields = {
        "call_id": call_id,
        "node": node,
        "action": action,
        "latency_ms": latency_ms,
        "llm_provider_used": llm_provider_used,
        "business_id": get_settings().business_id,
        **extra,
    }
    logger.log(level, message, extra={k: v for k, v in fields.items() if v is not None})


class Timer:
    """Context manager measuring wall-clock latency in milliseconds."""

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.latency_ms = round((time.perf_counter() - self._start) * 1000, 2)
        return False


# ---------------------------------------------------------------------------
# Twilio signature validation
# ---------------------------------------------------------------------------
async def validate_twilio_request(request: Request) -> dict:
    """FastAPI dependency: verify X-Twilio-Signature on every webhook.

    Returns the parsed form data so endpoints don't re-read the body.
    Raises 403 on invalid/missing signature (unless disabled for local dev).
    """
    settings = get_settings()
    form = dict(await request.form())

    if not settings.validate_twilio_signature:
        return form

    signature = request.headers.get("X-Twilio-Signature", "")
    # Twilio signs the *public* URL it called, not the internal one, so we
    # rebuild it from the configured public base + the request path.
    url = settings.public_base_url.rstrip("/") + request.url.path
    if request.url.query:
        url += "?" + request.url.query

    validator = RequestValidator(settings.twilio_auth_token)
    if not validator.validate(url, form, signature):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")
    return form
