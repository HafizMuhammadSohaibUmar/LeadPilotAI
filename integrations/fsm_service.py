"""GenericFSMService (Supabase-only fallback) + provider factory.

If a business has no FSM configured, leads simply live in Supabase and the
owner works them from SMS notifications — zero external dependencies.
"""
import logging
from uuid import uuid4

from config import get_settings
from middleware import log_event
from integrations.jobber_client import JobberService
from integrations.housecallpro_client import HousecallProService
from models.fsm import FSMCustomer, FSMJobRequest, FSMProvider, FSMResult

logger = logging.getLogger("fsm")


class GenericFSMService:
    """No external FSM: generate local IDs; the lead row in Supabase (written
    by lead_creation_node) IS the system of record."""

    async def create_customer(self, customer: FSMCustomer) -> FSMResult:
        customer_id = f"generic-cust-{uuid4()}"
        log_event(logger, "Generic FSM customer created",
                  action="fsm_generic_customer", fsm_customer_id=customer_id)
        return FSMResult(success=True, provider=FSMProvider.GENERIC,
                         customer_id=customer_id)

    async def create_job(self, job: FSMJobRequest) -> FSMResult:
        job_id = f"generic-job-{uuid4()}"
        log_event(logger, "Generic FSM job created",
                  action="fsm_generic_job", fsm_job_id=job_id)
        return FSMResult(success=True, provider=FSMProvider.GENERIC,
                         customer_id=job.customer_id, job_id=job_id)


def get_fsm_service():
    """Factory returning the configured FSM service for this tenant."""
    provider = get_settings().fsm_provider.lower()
    if provider == FSMProvider.JOBBER.value:
        return JobberService()
    if provider == FSMProvider.HOUSECALLPRO.value:
        return HousecallProService()
    return GenericFSMService()


async def create_fsm_lead(customer: FSMCustomer, title: str, description: str,
                          priority: str = "normal") -> FSMResult:
    """One-shot helper: create customer then job/request in the active FSM.

    Jobber calls its job object a 'request'; we normalize on create_request /
    create_job naming differences here so nodes.py stays provider-agnostic.
    """
    service = get_fsm_service()
    cust_result = await service.create_customer(customer)
    if not cust_result.success:
        return cust_result

    job = FSMJobRequest(customer_id=cust_result.customer_id, title=title,
                        description=description, priority=priority)
    if isinstance(service, JobberService):
        return await service.create_request(job)
    return await service.create_job(job)
