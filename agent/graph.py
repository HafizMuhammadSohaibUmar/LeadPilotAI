"""LangGraph wiring for the call-qualification state machine.

Topology (one graph invocation == one conversational turn):

            START
              │  route_turn()  ← EMERGENCY keyword intercept happens here,
              ▼                  from ANY state
   ┌─ greeting_node                    (asks name, END)
   ├─ service_identification_node     (classifies, END)
   ├─ urgency_assessment_node         (EMERGENCY/SAME_DAY/SCHEDULED, END)
   ├─ location_qualification_node     (address + zip check, END)
   ├─ contact_collection_node ──► routing_decision_node
   │                                   │ route_decision()
   │                 ┌─────────────────┼─────────────────┐
   │                 ▼                 ▼                 ▼
   ├─ emergency_escalation_node  lead_creation_node  polite_decline_node
   │                 └─────────────────┼─────────────────┘
   └───────────────────────────────────▼
                              call_summary_node ──► END  (always runs last)
"""
from langgraph.graph import END, START, StateGraph

from agent.state import CallState
from agent.nodes import (
    call_summary_node,
    contact_collection_node,
    emergency_escalation_node,
    greeting_node,
    lead_creation_node,
    location_qualification_node,
    polite_decline_node,
    route_decision,
    route_turn,
    routing_decision_node,
    service_identification_node,
    urgency_assessment_node,
)

# Nodes that ask the caller a question and wait for the next utterance;
# each turn ends the graph run so main.py can speak `agent_response`.
_TURN_NODES = [
    "greeting_node",
    "service_identification_node",
    "urgency_assessment_node",
    "location_qualification_node",
]

_TERMINAL_NODES = [
    "emergency_escalation_node",
    "lead_creation_node",
    "polite_decline_node",
]


def build_graph():
    graph = StateGraph(CallState)

    graph.add_node("greeting_node", greeting_node)
    graph.add_node("service_identification_node", service_identification_node)
    graph.add_node("urgency_assessment_node", urgency_assessment_node)
    graph.add_node("location_qualification_node", location_qualification_node)
    graph.add_node("contact_collection_node", contact_collection_node)
    graph.add_node("routing_decision_node", routing_decision_node)
    graph.add_node("emergency_escalation_node", emergency_escalation_node)
    graph.add_node("lead_creation_node", lead_creation_node)
    graph.add_node("polite_decline_node", polite_decline_node)
    graph.add_node("call_summary_node", call_summary_node)

    # Conditional entry: emergency intercept first, then resume at whatever
    # node the previous turn left in `current_node`.
    graph.add_conditional_edges(
        START,
        route_turn,
        {name: name for name in (_TURN_NODES + ["contact_collection_node",
                                                "emergency_escalation_node"])},
    )

    # Question nodes end the turn (their reply is spoken, then we wait).
    for name in _TURN_NODES:
        graph.add_edge(name, END)

    # Contact collection flows straight into the routing decision same-turn.
    graph.add_edge("contact_collection_node", "routing_decision_node")
    graph.add_conditional_edges(
        "routing_decision_node",
        route_decision,
        {name: name for name in _TERMINAL_NODES},
    )

    # Every terminal path funnels through call_summary_node — it ALWAYS runs.
    for name in _TERMINAL_NODES:
        graph.add_edge(name, "call_summary_node")
    graph.add_edge("call_summary_node", END)

    return graph.compile()


# Compiled once at import; stateless across calls (state is passed per turn).
agent_graph = build_graph()
