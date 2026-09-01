import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)

try:
    from network_as_code import NetworkAsCodeApi

    NAC_SDK_AVAILABLE = True
except ImportError:
    NAC_SDK_AVAILABLE = False


class LocationInput(BaseModel):
    latitude: float
    longitude: float


class AuditRequest(BaseModel):
    msisdn: str
    amount: float
    transaction_type: str = "WIRE_TRANSFER"
    current_location: LocationInput
    request_qod_slice: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ToolCallResult(BaseModel):
    name: str
    success: bool
    source: str
    duration_ms: Optional[float] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class AgentTraceItem(BaseModel):
    agent: str
    action: str
    thought: str
    status: str
    detail: str
    model: str | None = None
    provider: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class NokiaApiTelemetry(BaseModel):
    number_verification_match: Optional[bool] = None
    number_verification_status: str = "UNKNOWN"
    max_congestion_level: Optional[str] = None
    sim_swap_detected: bool = False
    last_sim_swap_date: Optional[str] = None
    location_verification_match: bool = True
    location_accuracy_meters: float = 120.0
    geofence_status: str = "VERIFIED"
    roaming_status: str = "DOMESTIC"
    roaming_country: Optional[str] = None
    reachability_status: str = "CONNECTED"
    qod_session_active: bool = False
    qod_profile: Optional[str] = None
    qod_status: Optional[str] = None
    tool_results: List[ToolCallResult] = Field(default_factory=list)
    evidence_strength: str = "MEDIUM"
    confidence: float = 0.65
    cross_border_risk: bool = False


class AuditResponse(BaseModel):
    msisdn: str
    amount: float
    transaction_type: str
    risk_score: str
    status: str
    telemetry: NokiaApiTelemetry
    reasoning: str
    recommended_action: str
    agent_trace: List[AgentTraceItem] = Field(default_factory=list)
    used_fallback: bool = False
    raw_output: Optional[str] = None
    diagnostics: Dict[str, Any] = Field(default_factory=dict)
