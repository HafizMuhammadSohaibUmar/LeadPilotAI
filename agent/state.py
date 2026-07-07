"""LangGraph conversation state for a single inbound call."""
from typing import Annotated, List, Optional
from typing_extensions import TypedDict

from langgraph.graph.message import add_messages

from models.call import CallOutcome, ServiceType, UrgencyLevel

# Emergency keywords checked at EVERY node, in ANY state.
# Match is case-insensitive substring on the caller's latest utterance.
EMERGENCY_KEYWORDS = [
    "burst pipe", "flooding", "gas leak", "no heat", "furnace out", "no ac",
    "no power", "electrical fire", "sparks", "smoke",
]


class CallState(TypedDict, total=False):
    # --- identity / tenancy ---
    business_id: str
    call_id: str
    caller_id: str            # caller phone number (Twilio From)
    caller_name: Optional[str]

    # --- qualification data ---
    service_type: Optional[ServiceType]
    urgency_level: Optional[UrgencyLevel]
    address: Optional[str]
    zip_code: Optional[str]
    is_in_service_area: Optional[bool]
    callback_number: Optional[str]

    # --- conversation ---
    # add_messages appends instead of replacing, preserving full history.
    messages: Annotated[List, add_messages]
    current_node: str
    agent_response: str       # last thing the agent said (sent to TTS)
    user_input: str           # last transcribed caller utterance

    # --- outcomes ---
    call_outcome: Optional[CallOutcome]
    fsm_lead_id: Optional[str]
    is_emergency_intercept: bool
    llm_provider_used: Optional[str]
    conversation_complete: bool


def detect_emergency(text: str) -> bool:
    """Return True if any emergency keyword appears in the utterance."""
    lowered = (text or "").lower()
    return any(keyword in lowered for keyword in EMERGENCY_KEYWORDS)
