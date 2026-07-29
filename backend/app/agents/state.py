from typing import List, Dict, Any, TypedDict
from pydantic import BaseModel

class LocationModel(BaseModel):
    latitude: float
    longitude: float

class AuditState(TypedDict):
    msisdn: str
    transaction_type: str
    amount: float
    location: Dict[str, float]
    sim_swap_cleared: bool
    location_cleared: bool
    risk_score: str
    status: str
    reasoning: str
    agent_trace: List[Dict[str, Any]]