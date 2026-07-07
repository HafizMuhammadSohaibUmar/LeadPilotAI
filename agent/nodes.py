"""LangGraph node implementations.

Design: the graph is invoked once per caller utterance (turn-based). A router
entry point dispatches to the node stored in `current_node`, after first
checking the EMERGENCY keyword intercept — so an emergency phrase spoken at
ANY point in the call jumps straight to escalation.

Nodes that need another caller reply set `agent_response` (spoken via TTS)
and stop the run; terminal nodes chain into call_summary_node, which always
runs last and persists the full call.
"""
import logging
from typing import Dict

from config import get_settings
from middleware import Timer, log_event
from integrations.llm_client import llm_client
from integrations.supabase_client import supabase_client
from integrations.twilio_client import twilio_client
from integrations.fsm_service import create_fsm_lead
from models.call import CallOutcome, LeadRecord, ServiceType, UrgencyLevel
from models.fsm import FSMCustomer
from agent import prompts
from agent.state import CallState, detect_emergency

logger = logging.getLogger("agent")


def _sys(content_kwargs: Dict = None) -> dict:
    settings = get_settings()
    return {"role": "system",
            "content": prompts.SYSTEM_PROMPT.format(
                business_name=settings.business_name)}


async def _llm(state: CallState, user_prompt: str, max_tokens: int = 30) -> str:
    """Single-shot structured extraction/classification call."""
    text, provider = await llm_client.complete(
        [_sys(), {"role": "user", "content": user_prompt}],
        call_id=state.get("call_id"), max_tokens=max_tokens,
    )
    # Track the provider that served the most recent turn for the calls table.
    state["llm_provider_used"] = provider
    return text


def _log_node(state: CallState, node: str, action: str, latency_ms=None):
    log_event(logger, f"{node}: {action}", call_id=state.get("call_id"),
              node=node, action=action, latency_ms=latency_ms,
              llm_provider_used=state.get("llm_provider_used"))


# ---------------------------------------------------------------------------
# 0. Router — emergency intercept + dispatch (used as conditional entry)
# ---------------------------------------------------------------------------
def route_turn(state: CallState) -> str:
    """Entry router. Emergency keywords override everything, from any state."""
    user_input = state.get("user_input", "")
    if user_input and detect_emergency(user_input):
        log_event(logger, "Emergency keyword intercept triggered",
                  call_id=state.get("call_id"), node="router",
                  action="emergency_intercept", utterance=user_input)
        return "emergency_escalation_node"
    return state.get("current_node") or "greeting_node"


# ---------------------------------------------------------------------------
# 1. greeting_node
# ---------------------------------------------------------------------------
async def greeting_node(state: CallState) -> Dict:
    settings = get_settings()
    greeting = prompts.GREETING_TEMPLATE.format(
        business_name=settings.business_name)
    _log_node(state, "greeting_node", "greeted_caller")
    return {
        "agent_response": greeting,
        "current_node": "service_identification_node",
        "messages": [{"role": "assistant", "content": greeting}],
    }


# ---------------------------------------------------------------------------
# 2. service_identification_node
# ---------------------------------------------------------------------------
async def service_identification_node(state: CallState) -> Dict:
    user_input = state.get("user_input", "")

    # First pass through this node captures the caller's name (asked by the
    # greeting), then asks what they need. Second pass classifies the need.
    if not state.get("caller_name"):
        with Timer() as timer:
            name = await _llm(state, prompts.NAME_EXTRACTION_PROMPT.format(
                user_input=user_input))
        name = "" if name.strip().upper() == "NONE" else name.strip()
        question = prompts.SERVICE_QUESTION.format(
            name_part=f", {name}" if name else "")
        _log_node(state, "service_identification_node", "captured_name",
                  timer.latency_ms)
        return {
            "caller_name": name or None,
            "agent_response": question,
            "current_node": "service_identification_node",
            "llm_provider_used": state.get("llm_provider_used"),
            "messages": [{"role": "user", "content": user_input},
                         {"role": "assistant", "content": question}],
        }

    with Timer() as timer:
        raw = await _llm(state, prompts.SERVICE_IDENTIFICATION_PROMPT.format(
            user_input=user_input))
    try:
        service = ServiceType(raw.strip().lower())
    except ValueError:
        service = ServiceType.OTHER  # unparseable answer -> safe default
    description = prompts.SERVICE_DESCRIPTIONS[service.value]
    question = prompts.URGENCY_QUESTION.format(service_description=description)
    _log_node(state, "service_identification_node",
              f"classified:{service.value}", timer.latency_ms)
    return {
        "service_type": service,
        "agent_response": question,
        "current_node": "urgency_assessment_node",
        "llm_provider_used": state.get("llm_provider_used"),
        "messages": [{"role": "user", "content": user_input},
                     {"role": "assistant", "content": question}],
    }


# ---------------------------------------------------------------------------
# 3. urgency_assessment_node
# ---------------------------------------------------------------------------
async def urgency_assessment_node(state: CallState) -> Dict:
    user_input = state.get("user_input", "")
    with Timer() as timer:
        raw = await _llm(state, prompts.URGENCY_ASSESSMENT_PROMPT.format(
            service_type=(state.get("service_type") or ServiceType.OTHER).value,
            user_input=user_input))
    try:
        urgency = UrgencyLevel(raw.strip().upper())
    except ValueError:
        urgency = UrgencyLevel.SAME_DAY  # ambiguous -> err on urgent side
    _log_node(state, "urgency_assessment_node",
              f"assessed:{urgency.value}", timer.latency_ms)
    return {
        "urgency_level": urgency,
        "agent_response": prompts.LOCATION_QUESTION,
        "current_node": "location_qualification_node",
        "llm_provider_used": state.get("llm_provider_used"),
        "messages": [{"role": "user", "content": user_input},
                     {"role": "assistant", "content": prompts.LOCATION_QUESTION}],
    }


# ---------------------------------------------------------------------------
# 4. location_qualification_node
# ---------------------------------------------------------------------------
async def location_qualification_node(state: CallState) -> Dict:
    settings = get_settings()
    user_input = state.get("user_input", "")
    with Timer() as timer:
        raw_zip = await _llm(state, prompts.ZIP_EXTRACTION_PROMPT.format(
            user_input=user_input))
    zip_code = raw_zip.strip()
    if zip_code.upper() == "NONE" or not zip_code.isdigit() or len(zip_code) != 5:
        # No usable ZIP heard — re-ask instead of guessing service area.
        retry = "Sorry, I need the five digit ZIP code too — what is it?"
        _log_node(state, "location_qualification_node", "zip_retry",
                  timer.latency_ms)
        return {
            "address": user_input,
            "agent_response": retry,
            "current_node": "location_qualification_node",
            "llm_provider_used": state.get("llm_provider_used"),
            "messages": [{"role": "user", "content": user_input},
                         {"role": "assistant", "content": retry}],
        }

    in_area = zip_code in settings.zip_list
    question = prompts.CONTACT_QUESTION.format(
        caller_number=state.get("caller_id", "the number you're calling from"))
    _log_node(state, "location_qualification_node",
              f"zip:{zip_code} in_area:{in_area}", timer.latency_ms)
    # Address may have been collected on a previous retry turn.
    address = state.get("address") or user_input
    if user_input not in (address or ""):
        address = f"{address} {user_input}".strip()
    return {
        "address": address,
        "zip_code": zip_code,
        "is_in_service_area": in_area,
        "agent_response": question,
        "current_node": "contact_collection_node",
        "llm_provider_used": state.get("llm_provider_used"),
        "messages": [{"role": "user", "content": user_input},
                     {"role": "assistant", "content": question}],
    }


# ---------------------------------------------------------------------------
# 5. contact_collection_node
# ---------------------------------------------------------------------------
async def contact_collection_node(state: CallState) -> Dict:
    user_input = state.get("user_input", "")
    caller_id = state.get("caller_id", "")
    with Timer() as timer:
        number = await _llm(state, prompts.CONTACT_CONFIRMATION_PROMPT.format(
            caller_number=caller_id, user_input=user_input))
    callback = number.strip() or caller_id
    _log_node(state, "contact_collection_node", "callback_confirmed",
              timer.latency_ms)
    # No agent_response here — routing_decision_node runs in the same turn
    # and the terminal node it picks will produce the spoken reply.
    return {
        "callback_number": callback,
        "current_node": "routing_decision_node",
        "llm_provider_used": state.get("llm_provider_used"),
        "messages": [{"role": "user", "content": user_input}],
    }


# ---------------------------------------------------------------------------
# 6. routing_decision_node (conditional edge)
# ---------------------------------------------------------------------------
async def routing_decision_node(state: CallState) -> Dict:
    _log_node(state, "routing_decision_node", "routing")
    return {}  # pure decision point; branching happens in route_decision()


def route_decision(state: CallState) -> str:
    """Conditional edge: EMERGENCY -> escalation, in-area -> lead,
    out-of-area -> polite decline."""
    if state.get("urgency_level") == UrgencyLevel.EMERGENCY:
        return "emergency_escalation_node"
    if state.get("is_in_service_area"):
        return "lead_creation_node"
    return "polite_decline_node"


# ---------------------------------------------------------------------------
# 7. emergency_escalation_node
# ---------------------------------------------------------------------------
async def emergency_escalation_node(state: CallState) -> Dict:
    settings = get_settings()
    call_id = state.get("call_id")
    caller = state.get("caller_name") or "Unknown caller"
    phone = state.get("callback_number") or state.get("caller_id", "unknown")
    service = (state.get("service_type") or ServiceType.OTHER).value
    address = state.get("address") or "address not collected"

    # 1) SMS the owner immediately — this must happen before anything else.
    await twilio_client.sms_owner(
        f"🚨 EMERGENCY at {settings.business_name}: {caller} ({phone}) — "
        f"{service} at {address}. Caller told help is on the way. "
        f"Call them back NOW.",
        call_id=call_id,
    )

    # 2) Create a HIGH_PRIORITY FSM job so it hits the dispatch board too.
    fsm_result = await create_fsm_lead(
        FSMCustomer(first_name=caller.split()[0] if caller else "Unknown",
                    last_name=" ".join(caller.split()[1:]),
                    phone=phone, address=address,
                    zip_code=state.get("zip_code")),
        title=f"EMERGENCY: {service}",
        description=f"Emergency call from {caller} ({phone}) at {address}.",
        priority="high",
    )

    _log_node(state, "emergency_escalation_node", "escalated")
    return {
        "urgency_level": UrgencyLevel.EMERGENCY,
        "call_outcome": CallOutcome.EMERGENCY_ESCALATED,
        "fsm_lead_id": fsm_result.job_id,
        "agent_response": prompts.EMERGENCY_RESPONSE,
        "current_node": "call_summary_node",
        "conversation_complete": True,
        "messages": [{"role": "assistant",
                      "content": prompts.EMERGENCY_RESPONSE}],
    }


# ---------------------------------------------------------------------------
# 8. lead_creation_node
# ---------------------------------------------------------------------------
async def lead_creation_node(state: CallState) -> Dict:
    settings = get_settings()
    call_id = state.get("call_id")
    caller = state.get("caller_name") or "Unknown"
    phone = state.get("callback_number") or state.get("caller_id", "")
    service = (state.get("service_type") or ServiceType.OTHER)
    urgency = (state.get("urgency_level") or UrgencyLevel.SCHEDULED)
    description = prompts.SERVICE_DESCRIPTIONS[service.value]

    # DUPLICATE DETECTION: same phone created a lead in the last 60 minutes?
    if await supabase_client.is_duplicate(phone):
        _log_node(state, "lead_creation_node", "duplicate_skipped")
        response = prompts.DUPLICATE_RESPONSE.format(caller_name=caller)
        return {
            "call_outcome": CallOutcome.DUPLICATE,
            "agent_response": response,
            "current_node": "call_summary_node",
            "conversation_complete": True,
            "messages": [{"role": "assistant", "content": response}],
        }

    # Push to the configured FSM (Jobber / HCP / generic).
    fsm_result = await create_fsm_lead(
        FSMCustomer(first_name=caller.split()[0] if caller else "Unknown",
                    last_name=" ".join(caller.split()[1:]),
                    phone=phone, address=state.get("address"),
                    zip_code=state.get("zip_code")),
        title=f"{urgency.value}: {service.value}",
        description=(f"{urgency.value} request from {caller} ({phone}) "
                     f"at {state.get('address')}."),
        priority="high" if urgency == UrgencyLevel.SAME_DAY else "normal",
    )

    # Persist the lead row in Supabase regardless of FSM outcome.
    lead = LeadRecord(
        business_id=settings.business_id,
        call_id=call_id or "",
        name=caller,
        phone=phone,
        service_type=service,
        urgency=urgency,
        address=state.get("address"),
        notes=f"Auto-qualified by voice agent. ZIP {state.get('zip_code')}.",
        fsm_system=fsm_result.provider.value,
        fsm_id=fsm_result.job_id,
    )
    try:
        await supabase_client.insert_lead(lead)
        await supabase_client.mark_lead_created(phone)
    except Exception as exc:
        # Lead persistence failure must not kill the live call.
        log_event(logger, f"Lead insert failed: {exc}", call_id=call_id,
                  node="lead_creation_node", action="lead_insert_failed",
                  level=logging.ERROR)

    # SMS confirmation to caller + summary to owner (fire-and-forget style).
    await twilio_client.send_sms(
        phone,
        f"Thanks {caller}! {settings.business_name} received your "
        f"{description} request. We'll call you back shortly.",
        call_id=call_id,
    )
    await twilio_client.sms_owner(
        f"New lead: {caller} ({phone}) — {service.value} / {urgency.value} "
        f"at {state.get('address')}. FSM: {fsm_result.provider.value} "
        f"#{fsm_result.job_id or 'n/a'}",
        call_id=call_id,
    )

    _log_node(state, "lead_creation_node", "lead_created")
    response = prompts.LEAD_CONFIRMATION.format(
        caller_name=caller, service_description=description)
    return {
        "call_outcome": CallOutcome.LEAD_CREATED,
        "fsm_lead_id": fsm_result.job_id,
        "agent_response": response,
        "current_node": "call_summary_node",
        "conversation_complete": True,
        "messages": [{"role": "assistant", "content": response}],
    }


# ---------------------------------------------------------------------------
# 9. polite_decline_node
# ---------------------------------------------------------------------------
async def polite_decline_node(state: CallState) -> Dict:
    settings = get_settings()
    response = prompts.OUT_OF_AREA_APOLOGY
    if settings.referral_business_name and settings.referral_business_phone:
        response += prompts.OUT_OF_AREA_REFERRAL.format(
            referral_name=settings.referral_business_name,
            referral_phone=settings.referral_business_phone)
    else:
        response += prompts.OUT_OF_AREA_CLOSE
    _log_node(state, "polite_decline_node", "declined_out_of_area")
    return {
        "call_outcome": CallOutcome.OUT_OF_AREA,
        "agent_response": response,
        "current_node": "call_summary_node",
        "conversation_complete": True,
        "messages": [{"role": "assistant", "content": response}],
    }


# ---------------------------------------------------------------------------
# 10. call_summary_node — ALWAYS runs last
# ---------------------------------------------------------------------------
async def call_summary_node(state: CallState) -> Dict:
    call_id = state.get("call_id")
    transcript = "\n".join(
        f"{getattr(m, 'type', None) or m.get('role', '?')}: "
        f"{getattr(m, 'content', None) or m.get('content', '')}"
        for m in state.get("messages", [])
    )
    fields = {
        "caller_name": state.get("caller_name"),
        "service_type": getattr(state.get("service_type"), "value", None),
        "urgency": getattr(state.get("urgency_level"), "value", None),
        "address": state.get("address"),
        "zip_code": state.get("zip_code"),
        "in_service_area": state.get("is_in_service_area"),
        "outcome": getattr(state.get("call_outcome"), "value",
                           CallOutcome.ABANDONED.value),
        "transcript": transcript,
        "fsm_lead_id": state.get("fsm_lead_id"),
        "llm_provider_used": state.get("llm_provider_used"),
    }
    try:
        await supabase_client.update_call(call_id, fields)
    except Exception as exc:
        log_event(logger, f"Call summary persist failed: {exc}",
                  call_id=call_id, node="call_summary_node",
                  action="summary_persist_failed", level=logging.ERROR)
    _log_node(state, "call_summary_node", "call_logged")
    return {"conversation_complete": True}
