"""Housecall Pro FSM integration via its REST API (async httpx)."""
import logging

import httpx

from config import get_settings
from middleware import Timer, log_event
from models.fsm import FSMCustomer, FSMJobRequest, FSMProvider, FSMResult

logger = logging.getLogger("housecallpro")


class HousecallProService:
    """Creates customers and jobs in Housecall Pro."""

    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.housecallpro_base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {settings.housecallpro_api_key}",
            "Content-Type": "application/json",
        }

    async def _post(self, path: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            with Timer() as timer:
                resp = await client.post(
                    f"{self.base_url}{path}", headers=self.headers, json=payload,
                )
            resp.raise_for_status()
        log_event(logger, f"Housecall Pro POST {path}", action="fsm_request",
                  latency_ms=timer.latency_ms)
        return resp.json()

    async def create_customer(self, customer: FSMCustomer) -> FSMResult:
        try:
            data = await self._post("/customers", {
                "first_name": customer.first_name,
                "last_name": customer.last_name or "Caller",
                "mobile_number": customer.phone,
                "addresses": (
                    [{"street": customer.address, "zip": customer.zip_code}]
                    if customer.address else []
                ),
            })
            return FSMResult(success=True, provider=FSMProvider.HOUSECALLPRO,
                             customer_id=str(data.get("id")))
        except Exception as exc:
            log_event(logger, f"HCP create_customer failed: {exc}",
                      action="fsm_error", level=logging.ERROR)
            return FSMResult(success=False, provider=FSMProvider.HOUSECALLPRO,
                             error=str(exc))

    async def create_job(self, job: FSMJobRequest) -> FSMResult:
        try:
            data = await self._post("/jobs", {
                "customer_id": job.customer_id,
                "description": job.description,
                "name": job.title,
                # HCP supports tags; surface urgency to dispatch board.
                "tags": ["HIGH_PRIORITY"] if job.priority == "high" else [],
            })
            return FSMResult(success=True, provider=FSMProvider.HOUSECALLPRO,
                             customer_id=job.customer_id,
                             job_id=str(data.get("id")))
        except Exception as exc:
            log_event(logger, f"HCP create_job failed: {exc}",
                      action="fsm_error", level=logging.ERROR)
            return FSMResult(success=False, provider=FSMProvider.HOUSECALLPRO,
                             error=str(exc))
