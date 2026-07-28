from langgraph.graph import StateGraph, END
from app.agents.state import AegisAgentState
from app.services.aduna_service import aduna_client
from app.agents.memory_agent import memory_engine

def security_node(state: AegisAgentState) -> AegisAgentState:
    res = aduna_client.check_sim_swap(state["phone_number"])
    state["sim_swap_detected"] = res.get("swapped", False)
    state["trace_logs"].append({
        "agent": "SecuritySpecialist (Groq)",
        "action": "CAMARA SIM Swap Check",
        "result": res
    })
    return state

def qod_slicing_node(state: AegisAgentState) -> AegisAgentState:
    if not state["sim_swap_detected"]:
        res = aduna_client.provision_qod_slice(state["phone_number"])
        state["qod_slice_active"] = True
        state["trace_logs"].append({
            "agent": "NetworkQoDAgent (Groq)",
            "action": "CAMARA 5G QoD Provisioned",
            "result": res
        })
    else:
        state["qod_slice_active"] = False
        state["trace_logs"].append({
            "agent": "NetworkQoDAgent (Groq)",
            "action": "CAMARA 5G QoD Blocked",
            "reason": "SIM Swap Detected"
        })
    return state

def audit_reasoning_node(state: AegisAgentState) -> AegisAgentState:
    # Memory Retrieval
    history = memory_engine.retrieve_past_incidents(state["phone_number"], state["event_context"])
    
    if state["sim_swap_detected"]:
        state["decision"] = "BLOCK"
        state["risk_score"] = 0.95
    else:
        state["decision"] = "ALLOW"
        state["risk_score"] = 0.05

    # Store into Mem0 + Qdrant
    memory_engine.store_security_event(
        phone_number=state["phone_number"],
        text=f"Event: {state['event_context']} -> Decision: {state['decision']}",
        metadata={"risk_score": state["risk_score"]}
    )

    state["trace_logs"].append({
        "agent": "RiskAuditor (Gemini 2.5 Pro)",
        "action": "Long-Term Memory Search & Decision",
        "decision": state["decision"],
        "historical_matches": len(history)
    })
    return state

# Construct LangGraph StateGraph
builder = StateGraph(AegisAgentState)
builder.add_node("security_check", security_node)
builder.add_node("qod_slicing", qod_slicing_node)
builder.add_node("audit_reasoning", audit_reasoning_node)

builder.set_entry_point("security_check")
builder.add_edge("security_check", "qod_slicing")
builder.add_edge("qod_slicing", "audit_reasoning")
builder.add_edge("audit_reasoning", END)

aegis_graph = builder.compile()