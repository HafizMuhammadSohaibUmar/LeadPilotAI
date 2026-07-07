"""Jobber FSM integration via its GraphQL API (async httpx)."""
import logging
from typing import Optional

import httpx

from config import get_settings
from middleware import Timer, log_event
from models.fsm import FSMCustomer, FSMJobRequest, FSMProvider, FSMResult

logger = logging.getLogger("jobber")

CREATE_CLIENT_MUTATION = """
mutation CreateClient($input: ClientCreateInput!) {
  clientCreate(input: $input) {
    client { id }
    userErrors { message }
  }
}
"""

CREATE_REQUEST_MUTATION = """
mutation CreateRequest($input: RequestCreateInput!) {
  requestCreate(input: $input) {
    request { id }
    userErrors { message }
  }
}
"""


class JobberService:
    """Creates customers and service requests in Jobber."""

    def __init__(self) -> None:
        settings = get_settings()
        self.url = settings.jobber_graphql_url
        self.headers = {
            "Authorization": f"Bearer {settings.jobber_api_token}",
            "Content-Type": "application/json",
            # Jobber requires an explicit API version header.
            "X-JOBBER-GRAPHQL-VERSION": "2023-11-15",
        }

    async def _graphql(self, query: str, variables: dict) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            with Timer() as timer:
                resp = await client.post(
                    self.url, headers=self.headers,
                    json={"query": query, "variables": variables},
                )
            resp.raise_for_status()
            data = resp.json()
        log_event(logger, "Jobber GraphQL call", action="fsm_request",
                  latency_ms=timer.latency_ms)
        if data.get("errors"):
            raise RuntimeError(str(data["errors"]))
        return data["data"]

    async def create_customer(self, customer: FSMCustomer) -> FSMResult:
        try:
            data = await self._graphql(CREATE_CLIENT_MUTATION, {
                "input": {
                    "firstName": customer.first_name,
                    "lastName": customer.last_name or "Caller",
                    "phones": [{"number": customer.phone, "primary": True}],
                }
            })
            payload = data["clientCreate"]
            if payload.get("userErrors"):
                raise RuntimeError(str(payload["userErrors"]))
            return FSMResult(success=True, provider=FSMProvider.JOBBER,
                             customer_id=payload["client"]["id"])
        except Exception as exc:
            log_event(logger, f"Jobber create_customer failed: {exc}",
                      action="fsm_error", level=logging.ERROR)
            return FSMResult(success=False, provider=FSMProvider.JOBBER,
                             error=str(exc))

    async def create_request(self, job: FSMJobRequest) -> FSMResult:
        try:
            data = await self._graphql(CREATE_REQUEST_MUTATION, {
                "input": {
                    "clientId": job.customer_id,
                    "title": job.title,
                    # Jobber has no priority field on requests; encode urgency
                    # in the title/description so dispatchers see it.
                    "instructions": job.description,
                }
            })
            payload = data["requestCreate"]
            if payload.get("userErrors"):
                raise RuntimeError(str(payload["userErrors"]))
            return FSMResult(success=True, provider=FSMProvider.JOBBER,
                             customer_id=job.customer_id,
                             job_id=payload["request"]["id"])
        except Exception as exc:
            log_event(logger, f"Jobber create_request failed: {exc}",
                      action="fsm_error", level=logging.ERROR)
            return FSMResult(success=False, provider=FSMProvider.JOBBER,
                             error=str(exc))
