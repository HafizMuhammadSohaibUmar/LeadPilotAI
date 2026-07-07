"""Models for Field Service Management (FSM) integrations (Jobber, Housecall Pro)."""
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class FSMProvider(str, Enum):
    JOBBER = "jobber"
    HOUSECALLPRO = "housecallpro"
    GENERIC = "generic"  # Supabase-only fallback when no FSM is configured


class FSMCustomer(BaseModel):
    first_name: str
    last_name: str = ""
    phone: str
    address: Optional[str] = None
    zip_code: Optional[str] = None


class FSMJobRequest(BaseModel):
    customer_id: str
    title: str
    description: str
    priority: str = "normal"  # "high" for emergencies


class FSMResult(BaseModel):
    """Uniform result returned by every FSM service implementation."""
    success: bool
    provider: FSMProvider
    customer_id: Optional[str] = None
    job_id: Optional[str] = None
    error: Optional[str] = None
