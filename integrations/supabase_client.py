"""Async Supabase (PostgreSQL) access via the REST (PostgREST) API using httpx.

We deliberately use the raw REST API instead of the supabase-py SDK: it keeps
the dependency tree tiny, is fully async, and is trivial to mock in tests.
Every row is scoped by business_id (multi-tenant from day one).
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from config import get_settings
from middleware import Timer, log_event
from models.call import CallRecord, LeadRecord

logger = logging.getLogger("supabase")


def _mask_phone(phone: str | None) -> str:
    if not phone:
        return ""
    return f"{phone[:3]}***{phone[-4:]}" if len(phone) >= 7 else "***"


class SupabaseClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.supabase_url.rstrip("/")
        self.headers = {
            "apikey": settings.supabase_key,
            "Authorization": f"Bearer {settings.supabase_key}",
            "Content-Type": "application/json",
            # Return the inserted/updated representation so we get IDs back.
            "Prefer": "return=representation",
        }
        self.business_id = settings.business_id
        self.dup_window = timedelta(minutes=settings.duplicate_window_minutes)

    def _url(self, table: str) -> str:
        return f"{self.base_url}/rest/v1/{table}"

    async def _request(self, method: str, table: str, *,
                       params: Optional[dict] = None,
                       json: Any = None) -> List[Dict]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            with Timer() as timer:
                resp = await client.request(
                    method, self._url(table), headers=self.headers,
                    params=params, json=json,
                )
            resp.raise_for_status()
            log_event(logger, f"supabase {method} {table}",
                      action="db_request", latency_ms=timer.latency_ms)
            return resp.json() if resp.content else []

    # ------------------------------------------------------------------ calls
    async def insert_call(self, call: CallRecord) -> Dict:
        rows = await self._request("POST", "calls",
                                   json=call.model_dump(mode="json"))
        return rows[0] if rows else {}

    async def update_call(self, call_id: str, fields: Dict) -> Dict:
        rows = await self._request(
            "PATCH", "calls",
            params={"id": f"eq.{call_id}",
                    "business_id": f"eq.{self.business_id}"},
            json=fields,
        )
        return rows[0] if rows else {}

    # ------------------------------------------------------------------ leads
    async def insert_lead(self, lead: LeadRecord) -> Dict:
        rows = await self._request("POST", "leads",
                                   json=lead.model_dump(mode="json"))
        return rows[0] if rows else {}

    # ------------------------------------------------------ duplicate check
    async def is_duplicate(self, phone: str) -> bool:
        """True if this phone created a lead within the dedup window."""
        rows = await self._request(
            "GET", "duplicate_check",
            params={"phone": f"eq.{phone}",
                    "business_id": f"eq.{self.business_id}",
                    "select": "phone,last_seen"},
        )
        if not rows:
            return False
        last_seen = datetime.fromisoformat(
            rows[0]["last_seen"].replace("Z", "+00:00"))
        return datetime.now(timezone.utc) - last_seen < self.dup_window

    async def mark_lead_created(self, phone: str) -> None:
        """Upsert the dedup marker for this phone."""
        headers = {**self.headers,
                   "Prefer": "resolution=merge-duplicates,return=representation"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                self._url("duplicate_check"),
                headers=headers,
                params={"on_conflict": "business_id,phone"},
                json={"business_id": self.business_id, "phone": phone,
                      "last_seen": datetime.now(timezone.utc).isoformat()},
            )
            resp.raise_for_status()

    # ----------------------------------------------------------------- health
    async def health_check(self) -> dict:
        try:
            with Timer() as timer:
                await self._request("GET", "calls",
                                    params={"select": "id", "limit": "1"})
            return {"ok": True, "latency_ms": timer.latency_ms}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def demo_snapshot(self) -> dict:
        calls = await self._request(
            "GET", "calls",
            params={
                "business_id": f"eq.{self.business_id}",
                "select": "caller_number,service_type,urgency,outcome,duration_seconds,created_at",
                "order": "created_at.desc",
                "limit": "6",
            },
        )
        leads = await self._request(
            "GET", "leads",
            params={
                "business_id": f"eq.{self.business_id}",
                "select": "phone,service_type,urgency,status,created_at",
                "order": "created_at.desc",
                "limit": "6",
            },
        )
        return {
            "tables": {
                "calls": {
                    "sample": [
                        {
                            "phone": _mask_phone(row.get("caller_number")),
                            "service_type": row.get("service_type"),
                            "urgency": row.get("urgency"),
                            "outcome": row.get("outcome"),
                            "duration_seconds": row.get("duration_seconds"),
                            "created_at": row.get("created_at"),
                        }
                        for row in calls
                    ],
                },
                "leads": {
                    "sample": [
                        {
                            "phone": _mask_phone(row.get("phone")),
                            "service_type": row.get("service_type"),
                            "urgency": row.get("urgency"),
                            "status": row.get("status"),
                            "created_at": row.get("created_at"),
                        }
                        for row in leads
                    ],
                },
            }
        }


supabase_client = SupabaseClient()
