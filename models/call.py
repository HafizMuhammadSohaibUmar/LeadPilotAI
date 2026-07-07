"""Pydantic models mirroring the Supabase `calls` / `leads` tables."""
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class ServiceType(str, Enum):
    HVAC_REPAIR = "hvac_repair"
    HVAC_MAINTENANCE = "hvac_maintenance"
    PLUMBING_EMERGENCY = "plumbing_emergency"
    PLUMBING_ROUTINE = "plumbing_routine"
    ROOFING_INSPECTION = "roofing_inspection"
    ROOFING_REPAIR = "roofing_repair"
    ELECTRICAL = "electrical"
    PEST_CONTROL = "pest_control"
    GARAGE_DOOR = "garage_door"
    OTHER = "other"


class UrgencyLevel(str, Enum):
    EMERGENCY = "EMERGENCY"
    SAME_DAY = "SAME_DAY"
    SCHEDULED = "SCHEDULED"


class CallOutcome(str, Enum):
    LEAD_CREATED = "lead_created"
    EMERGENCY_ESCALATED = "emergency_escalated"
    OUT_OF_AREA = "out_of_area"
    DUPLICATE = "duplicate"
    VOICEMAIL = "voicemail"  # graceful degradation when LLM chain is down
    ABANDONED = "abandoned"  # caller hung up / silence timeout
    IN_PROGRESS = "in_progress"


class CallRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    business_id: str
    caller_number: str
    caller_name: Optional[str] = None
    service_type: Optional[ServiceType] = None
    urgency: Optional[UrgencyLevel] = None
    address: Optional[str] = None
    zip_code: Optional[str] = None
    in_service_area: Optional[bool] = None
    outcome: CallOutcome = CallOutcome.IN_PROGRESS
    transcript: str = ""
    fsm_lead_id: Optional[str] = None
    duration_seconds: int = 0
    llm_provider_used: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class LeadRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    business_id: str
    call_id: str
    name: Optional[str] = None
    phone: str
    service_type: Optional[ServiceType] = None
    urgency: Optional[UrgencyLevel] = None
    address: Optional[str] = None
    notes: str = ""
    fsm_system: str = "generic"
    fsm_id: Optional[str] = None
    status: str = "new"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
