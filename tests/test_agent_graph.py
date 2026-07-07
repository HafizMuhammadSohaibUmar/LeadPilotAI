"""Tests for the LangGraph conversation state machine.

llm_client / supabase / twilio / FSM are mocked so we exercise pure graph
logic: routing, emergency intercept, duplicate detection, terminal outcomes.
"""
import pytest
from unittest.mock import AsyncMock, patch

import agent.nodes as nodes
from agent.graph import agent_graph
from agent.state import EMERGENCY_KEYWORDS, detect_emergency
from agent.nodes import route_decision, route_turn
from models.call import CallOutcome, ServiceType, UrgencyLevel


def _state(**overrides):
    base = {
        "business_id": "test-business",
        "call_id": "CA123",
        "caller_id": "+15559998888",
        "messages": [],
        "current_node": "greeting_node",
        "user_input": "",
        "is_emergency_intercept": False,
        "conversation_complete": False,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Emergency keyword intercept — from ANY state
# ---------------------------------------------------------------------------
def test_detect_emergency_matches_all_keywords():
    for keyword in EMERGENCY_KEYWORDS:
        assert detect_emergency(f"help, there is a {keyword} here!")


def test_detect_emergency_negative():
    assert not detect_emergency("I'd like to schedule a roof inspection")
    assert not detect_emergency("")


@pytest.mark.parametrize("current_node", [
    "greeting_node", "service_identification_node",
    "urgency_assessment_node", "location_qualification_node",
    "contact_collection_node",
])
def test_route_turn_emergency_intercept_from_any_state(current_node):
    state = _state(current_node=current_node,
                   user_input="there's a gas leak in my kitchen")
    assert route_turn(state) == "emergency_escalation_node"


def test_route_turn_resumes_current_node_without_emergency():
    state = _state(current_node="urgency_assessment_node",
                   user_input="my AC is making a weird noise")
    assert route_turn(state) == "urgency_assessment_node"


# ---------------------------------------------------------------------------
# routing_decision_node conditional edge
# ---------------------------------------------------------------------------
def test_route_decision_emergency():
    state = _state(urgency_level=UrgencyLevel.EMERGENCY,
                   is_in_service_area=True)
    assert route_decision(state) == "emergency_escalation_node"


def test_route_decision_in_area_lead():
    state = _state(urgency_level=UrgencyLevel.SCHEDULED,
                   is_in_service_area=True)
    assert route_decision(state) == "lead_creation_node"


def test_route_decision_out_of_area():
    state = _state(urgency_level=UrgencyLevel.SAME_DAY,
                   is_in_service_area=False)
    assert route_decision(state) == "polite_decline_node"


# ---------------------------------------------------------------------------
# Full graph turns (happy path)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_greeting_turn():
    result = await agent_graph.ainvoke(_state())
    assert "Test HVAC Co" in result["agent_response"]
    assert result["current_node"] == "service_identification_node"


@pytest.mark.asyncio
async def test_full_lead_creation_flow():
    """Walk the whole conversation to a created lead (in service area)."""
    llm_answers = iter([
        "Jane",             # name extraction
        "hvac_repair",      # service classification
        "SCHEDULED",        # urgency
        "78701",            # zip extraction (in area)
        "+15559998888",     # callback confirmation
    ])

    async def fake_complete(messages, **kwargs):
        return next(llm_answers), "groq/llama-3.3-70b-versatile"

    with patch.object(nodes.llm_client, "complete", new=fake_complete), \
         patch.object(nodes.supabase_client, "is_duplicate",
                      new=AsyncMock(return_value=False)), \
         patch.object(nodes.supabase_client, "insert_lead",
                      new=AsyncMock(return_value={})) as mock_lead, \
         patch.object(nodes.supabase_client, "mark_lead_created",
                      new=AsyncMock()), \
         patch.object(nodes.supabase_client, "update_call",
                      new=AsyncMock(return_value={})), \
         patch.object(nodes.twilio_client, "send_sms",
                      new=AsyncMock(return_value=True)) as mock_sms, \
         patch.object(nodes.twilio_client, "sms_owner",
                      new=AsyncMock(return_value=True)) as mock_owner_sms:

        state = _state()
        # Turn 1: greeting
        state.update(await agent_graph.ainvoke(state))
        # Turn 2: caller gives name
        state["user_input"] = "Hi, this is Jane"
        state.update(await agent_graph.ainvoke(state))
        assert state["caller_name"] == "Jane"
        # Turn 3: describes problem
        state["user_input"] = "My air conditioner stopped cooling"
        state.update(await agent_graph.ainvoke(state))
        assert state["service_type"] == ServiceType.HVAC_REPAIR
        # Turn 4: urgency
        state["user_input"] = "Sometime this week is fine"
        state.update(await agent_graph.ainvoke(state))
        assert state["urgency_level"] == UrgencyLevel.SCHEDULED
        # Turn 5: address (in-area zip)
        state["user_input"] = "100 Congress Ave, Austin, 78701"
        state.update(await agent_graph.ainvoke(state))
        assert state["is_in_service_area"] is True
        # Turn 6: confirm callback -> routing -> lead -> summary
        state["user_input"] = "Yes, that number works"
        state.update(await agent_graph.ainvoke(state))

    assert state["call_outcome"] == CallOutcome.LEAD_CREATED
    assert state["conversation_complete"] is True
    mock_lead.assert_awaited_once()
    mock_sms.assert_awaited_once()        # caller confirmation SMS
    mock_owner_sms.assert_awaited_once()  # owner summary SMS


@pytest.mark.asyncio
async def test_emergency_intercept_mid_call_escalates():
    """Failure/edge path: emergency phrase spoken during location step jumps
    straight to escalation, SMSes the owner, and creates a high-priority job."""
    fsm_result = AsyncMock()
    with patch.object(nodes.twilio_client, "sms_owner",
                      new=AsyncMock(return_value=True)) as mock_owner_sms, \
         patch.object(nodes, "create_fsm_lead",
                      new=AsyncMock()) as mock_fsm, \
         patch.object(nodes.supabase_client, "update_call",
                      new=AsyncMock(return_value={})):
        mock_fsm.return_value.job_id = "job-99"
        state = _state(current_node="location_qualification_node",
                       caller_name="Bob",
                       service_type=ServiceType.PLUMBING_EMERGENCY,
                       user_input="wait, actually there's a burst pipe flooding everything")
        result = await agent_graph.ainvoke(state)

    assert result["call_outcome"] == CallOutcome.EMERGENCY_ESCALATED
    assert result["urgency_level"] == UrgencyLevel.EMERGENCY
    mock_owner_sms.assert_awaited_once()
    mock_fsm.assert_awaited_once()
    assert mock_fsm.await_args.kwargs.get("priority") == "high"
    assert "help is on the way" in result["agent_response"].lower()


@pytest.mark.asyncio
async def test_duplicate_caller_skips_lead_creation():
    """Failure path: same phone within 60 min -> log duplicate, no new lead."""
    with patch.object(nodes.supabase_client, "is_duplicate",
                      new=AsyncMock(return_value=True)), \
         patch.object(nodes.supabase_client, "insert_lead",
                      new=AsyncMock()) as mock_lead, \
         patch.object(nodes.supabase_client, "update_call",
                      new=AsyncMock(return_value={})), \
         patch.object(nodes.twilio_client, "send_sms",
                      new=AsyncMock()) as mock_sms:
        result = await nodes.lead_creation_node(_state(
            caller_name="Jane", callback_number="+15559998888",
            service_type=ServiceType.HVAC_REPAIR,
            urgency_level=UrgencyLevel.SCHEDULED,
            is_in_service_area=True,
        ))

    assert result["call_outcome"] == CallOutcome.DUPLICATE
    mock_lead.assert_not_awaited()
    mock_sms.assert_not_awaited()


@pytest.mark.asyncio
async def test_out_of_area_polite_decline():
    with patch.object(nodes.supabase_client, "update_call",
                      new=AsyncMock(return_value={})):
        state = _state(current_node="contact_collection_node",
                       caller_name="Sam",
                       urgency_level=UrgencyLevel.SCHEDULED,
                       is_in_service_area=False,
                       zip_code="10001",
                       user_input="yes that number is fine")
        with patch.object(nodes.llm_client, "complete",
                          new=AsyncMock(return_value=("+15559998888", "groq/llama-3.3-70b-versatile"))):
            result = await agent_graph.ainvoke(state)

    assert result["call_outcome"] == CallOutcome.OUT_OF_AREA
    assert "sorry" in result["agent_response"].lower()
