import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)

try:
    from network_as_code import NetworkAsCodeApi

    NAC_SDK_AVAILABLE = True
except ImportError:
    NAC_SDK_AVAILABLE = False


# E.164: leading '+', non-zero country code, 8-15 digits total.
MSISDN_PATTERN = r"^\+[1-9]\d{7,14}$"

# The transaction surface AegisTel models. Unknown/foreign codes are rejected
# loudly (422) instead of being silently treated as a default.
TRANSACTION_TYPES = {
    "WIRE_TRANSFER",
    "SAME_DAY_WIRE",
    "CROSS_BORDER_SWIFT",
    "GIFT_CARD_TOPUP",
    "P2P_TRANSFER",
    "P2P",
    "MOBILE_PAYMENT",
    "INSTANT_PAYMENT",
    "BILL_PAYMENT",
}

# Bound free-form risk-context metadata so a giant payload cannot swamp the
# decision model or the audit log.
MAX_METADATA_KEYS = 32
MAX_METADATA_BYTES = 8192


class LocationInput(BaseModel):
    latitude: float = Field(..., ge=-90, le=90, description="WGS84 latitude in [-90, 90]")
    longitude: float = Field(..., ge=-180, le=180, description="WGS84 longitude in [-180, 180]")


class AuditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    msisdn: str = Field(..., pattern=MSISDN_PATTERN, description="Subscriber MSISDN in E.164 format, e.g. +966500000001")
    amount: float = Field(..., gt=0, le=1_000_000_000, description="Transaction amount in local currency units")
    transaction_type: str = "WIRE_TRANSFER"
    current_location: LocationInput
    request_qod_slice: bool = False
    # NOTE: there is intentionally NO tenant field here. The tenant namespace is
    # derived server-side from the API client's credential (see `_resolve_tenant`
    # in main.py) and is never accepted from public JSON. `extra="forbid"` makes
    # a client that tries to smuggle `tenant_id` in the body fail loudly (422)
    # instead of being silently ignored.
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("transaction_type")
    @classmethod
    def _normalize_and_validate_transaction_type(cls, value: str) -> str:
        normalized = (value or "").strip().upper()
        if normalized not in TRANSACTION_TYPES:
            raise ValueError(
                f"Unsupported transaction_type {value!r}. Must be one of: {', '.join(sorted(TRANSACTION_TYPES))}"
            )
        return normalized

    @field_validator("metadata")
    @classmethod
    def _bound_metadata(cls, value: Any) -> Dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("metadata must be a JSON object")
        if len(value) > MAX_METADATA_KEYS:
            raise ValueError(f"metadata exceeds {MAX_METADATA_KEYS} keys")
        try:
            body = json.dumps(value, separators=(",", ":"), default=str, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("metadata could not be serialized") from exc
        if len(body.encode("utf-8")) > MAX_METADATA_BYTES:
            raise ValueError(f"metadata payload exceeds {MAX_METADATA_BYTES} bytes")
        return value


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
    evidence_summary: Dict[str, int] = Field(default_factory=dict)


class AuditResponse(BaseModel):
    audit_id: UUID = Field(default_factory=uuid4)
    msisdn: str
    amount: float
    transaction_type: str
    tenant_id: str = "demo"
    risk_score: str
    status: str
    # A recommendation ONLY. This decision pipeline never provisions a QoD
    # session; it only signals that a step-up could be closed with one. The
    # session itself is created by an explicit, authenticated, policy-gated
    # confirm action (POST /api/v1/audit/qod/provision).
    qod_recommended: bool = False
    telemetry: NokiaApiTelemetry
    reasoning: str
    recommended_action: str
    agent_trace: List[AgentTraceItem] = Field(default_factory=list)
    used_fallback: bool = False
    raw_output: Optional[str] = None
    diagnostics: Dict[str, Any] = Field(default_factory=dict)


class QoDProvisionRequest(BaseModel):
    """Explicit, consented request to provision a QoD session for a MSISDN.

    Provisioning a QoD session lends a chargeable shared network resource to a
    subscriber for a bounded duration, so this is deliberately a SEPARATE action
    from the audit decision (which only returns `qod_recommended`). The endpoint
    additionally requires an authenticated client (operator admin key or tenant
    API key) and the server-side bank-policy flag AEGISTEL_QOD_POLICY_ENABLED.
    """

    model_config = ConfigDict(extra="forbid")

    audit_id: UUID = Field(..., description="Fresh tenant-scoped audit that recommended QoD.")
    msisdn: str = Field(..., pattern=MSISDN_PATTERN, description="Subscriber MSISDN in E.164 format.")
    profile: str = Field(default="QOS_E", description="CAMARA QoS profile label (e.g. QOS_E).")
    duration_seconds: int = Field(default=3600, ge=60, le=86400, description="Bounded session duration in seconds.")
