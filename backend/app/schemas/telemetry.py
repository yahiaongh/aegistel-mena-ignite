import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.core.config import settings

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
    transaction_type: str
    current_location: LocationInput
    request_qod_slice: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ToolCallResult(BaseModel):
    name: str
    success: bool
    source: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class AgentTraceItem(BaseModel):
    agent: str
    action: str
    thought: str
    status: str
    detail: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class NokiaApiTelemetry(BaseModel):
    number_verification_match: bool = False
    sim_swap_detected: bool = False
    sim_swap_age_hours: Optional[int] = None
    location_verification_match: bool = True
    location_accuracy_meters: float = 120.0
    geofence_status: str = "INSIDE"
    roaming_status: str = "DOMESTIC"
    roaming_country: Optional[str] = None
    reachability_status: str = "CONNECTED"
    qod_session_active: bool = False
    qod_profile: Optional[str] = None
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


class CamaraNetworkAsCodeClient:
    """Nokia Network as Code client wrapper with sandbox-friendly fallbacks."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.NOKIA_NAC_API_KEY
        self.nac_host = settings.NOKIA_NAC_HOST
        self.nac_api = None

        if NAC_SDK_AVAILABLE and self.api_key:
            try:
                self.nac_api = NetworkAsCodeApi(api_key=self.api_key, rapidapi_host=self.nac_host)
                logger.info("Initialized official Nokia Network as Code SDK client.")
            except Exception as exc:
                logger.warning(f"Failed to initialize NetworkAsCodeApi SDK: {exc}")

    def check_sim_swap(self, msisdn: str, max_age: int = 240) -> Dict[str, Any]:
        if self.nac_api:
            try:
                result = self.nac_api.sim_swap.check(phone_number=msisdn, max_age=max_age)
                return {
                    "swapped": getattr(result, "swapped", False),
                    "status_code": 200,
                    "source": "Nokia Network as Code Live API",
                }
            except Exception as exc:
                logger.error(f"Nokia NaC SIM Swap SDK call failed: {exc}")

        if msisdn in ["+99999991000", "+99999991002"] or msisdn.endswith("1000") or msisdn.endswith("1002"):
            return {
                "swapped": True,
                "status_code": 200,
                "detail": "SIM swap detected within the specified max_age timeframe.",
                "source": "Nokia NaC Test Sandbox",
            }

        return {
            "swapped": False,
            "status_code": 200,
            "detail": "No recent SIM swap detected for target device.",
            "source": "Nokia NaC Test Sandbox",
        }

    def verify_location(self, msisdn: str, latitude: float, longitude: float, radius: int = 10000) -> Dict[str, Any]:
        if latitude == 0.0 and longitude == 0.0:
            return {
                "verificationResult": "FALSE",
                "reason": "INVALID_LOCATION_NULL_ISLAND",
                "status_code": 200,
                "source": "Nokia NaC Guardrail Engine",
            }

        if self.nac_api:
            try:
                res = self.nac_api.location.verify_v1(
                    device={"phone_number": msisdn},
                    latitude=latitude,
                    longitude=longitude,
                    radius=radius,
                )
                verification_val = str(getattr(res, "verification_result", "TRUE")).upper()
                return {
                    "verificationResult": verification_val,
                    "status_code": 200,
                    "source": "Nokia Network as Code Live API",
                }
            except Exception as exc:
                logger.error(f"Nokia NaC Location Verification SDK call failed: {exc}")

        return {
            "verificationResult": "TRUE",
            "status_code": 200,
            "source": "Nokia NaC Test Sandbox",
        }


camara_client = CamaraNetworkAsCodeClient()