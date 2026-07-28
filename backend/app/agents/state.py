from typing import TypedDict, List, Dict, Any

class AegisAgentState(TypedDict):
    event_context: str
    phone_number: str
    security_clearance: bool
    sim_swap_detected: bool
    qod_slice_active: bool
    risk_score: float
    audit_memory_id: str
    decision: str
    trace_logs: List[Dict[str, Any]]