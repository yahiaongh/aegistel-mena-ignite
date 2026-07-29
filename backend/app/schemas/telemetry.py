from typing import List, Dict, Any
from pydantic import BaseModel, Field


class LocationSchema(BaseModel):
    latitude: float
    longitude: float


class AuditRequest(BaseModel):
    msisdn: str = Field(..., description="Phone number with country code, e.g., +99999991001")
    transaction_type: str = Field(..., description="Type of transaction, e.g., WIRE_TRANSFER")
    amount: float = Field(..., description="Amount being transferred")
    location: LocationSchema = Field(..., description="Geographic location of the device")


class AgentTraceItem(BaseModel):
    agent: str
    action: str
    thought: str
    status: str
    detail: str


class AuditResponse(BaseModel):
    msisdn: str
    transaction_type: str
    amount: float
    location: LocationSchema
    sim_swap_cleared: bool
    location_cleared: bool
    risk_score: str
    status: str
    reasoning: str
    agent_trace: List[AgentTraceItem]