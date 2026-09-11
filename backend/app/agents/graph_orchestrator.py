import asyncio
import json
import re
import time
import uuid
from typing import Annotated, Any, Dict, List, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from app.agents.crew_specialists import run_specialist_crew, synthesize_specialist_assessment
from app.agents.memory_agent import memory_engine
from app.core.config import settings
from app.schemas.telemetry import (
    AgentTraceItem,
    AuditRequest,
    AuditResponse,
    NokiaApiTelemetry,
    ToolCallResult,
)


class FinalAssessment(BaseModel):
    status: Literal["APPROVED", "REJECTED", "BLOCKED", "STEP_UP_REQUIRED", "MANUAL_REVIEW"] = Field(...)
    risk_score: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = Field(...)
    sim_swap_detected: bool = Field(False)
    last_sim_swap_date: str | None = Field(None)
    location_verification_match: bool = Field(True)
    location_accuracy_meters: float = Field(120.0)
    geofence_status: str = Field("VERIFIED")
    roaming_status: str = Field("DOMESTIC")
    roaming_country: str | None = Field(None)
    reachability_status: str = Field("UNKNOWN")
    number_verification_match: bool | None = Field(None)
    number_verification_status: str = Field("UNKNOWN")
    max_congestion_level: str | None = Field(None)
    qod_session_active: bool = Field(False)
    qod_profile: str | None = Field(None)
    qod_status: str | None = Field(None)
    device_swap_detected: bool | None = Field(None)
    number_recycled: bool | None = Field(None)
    reasoning: str = Field(...)
    recommended_action: str = Field(...)


class AuditState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    assessment: FinalAssessment | None
    memory_context: List[Dict[str, Any]]
    tool_results: List[Dict[str, Any]]
    errors: List[str]
    request_context: Dict[str, Any]
    specialist_output: Dict[str, Any]
    progress_callback: Any | None


SYSTEM_PROMPT = """You are AegisTel's Autonomous Telecom Fraud Detection Agent.
Your objective is to assess transaction risk using live Nokia Network as Code CAMARA telemetry.

Guidelines:
1. Use the available telemetry tools and crew specialists to build a grounded fraud evaluation.
2. If the evidence suggests a high-risk or cross-border event, trigger a QoD step-up session.
3. Keep the reasoning concise but evidence-driven.
"""


def _compute_confidence(tool_results: list[dict]) -> float:
    """Compute a simple per-request confidence based on how many tools returned
    live Nokia SDK data versus fallbacks. Returns a value in [0.5, 0.9]."""
    live_count = sum(1 for r in tool_results if r.get("source") == "Nokia NaC SDK")
    total = len(tool_results) or 1
    base = 0.5 + 0.4 * (live_count / total)
    return round(base, 2)


def _tool_succeeded(item: Dict[str, Any]) -> bool:
    """Derive a tool call's success from its result payload, so the evidence
    trail stays internally consistent: a payload carrying an error or a
    failing HTTP status is reported as a failure, never as a clean success.

    Success requires BOTH: no `error` field AND an HTTP status below 400.
    Payloads without a status_code (pure result objects) are presumed success
    when they carry no error.
    """
    if item.get("error"):
        return False
    status = item.get("status_code")
    if status is None:
        return True
    try:
        return int(status) < 400
    except (TypeError, ValueError):
        return False


def _extract_request_context(messages: List[BaseMessage]) -> Dict[str, Any]:
    human_message = next((msg for msg in reversed(messages) if isinstance(msg, HumanMessage)), None)
    if not human_message:
        return {}
    content = human_message.content or ""
    amount_match = re.search(r"Amount:\s*\$([0-9,\.]+)", content)
    amount = float(amount_match.group(1).replace(",", "")) if amount_match else 0.0
    msisdn_match = re.search(r"MSISDN:\s*(.+)", content)
    msisdn = msisdn_match.group(1).strip() if msisdn_match else ""
    coord_match = re.search(r"Coordinates:\s*Lat\s*([-0-9.]+),\s*Lon\s*([-0-9.]+)", content)
    latitude = float(coord_match.group(1)) if coord_match else 24.7
    longitude = float(coord_match.group(2)) if coord_match else 46.7
    qod_match = re.search(r"Request QoD:\s*(.+)", content)
    request_qod = qod_match.group(1).strip().lower() == "true" if qod_match else False
    return {
        "msisdn": msisdn,
        "amount": amount,
        "latitude": latitude,
        "longitude": longitude,
        "request_qod": request_qod,
    }


async def crew_node(state: AuditState) -> Dict[str, Any]:
    request_context = state.get("request_context", {})
    specialist_output = await asyncio.to_thread(
        run_specialist_crew,
        request_context,
        state.get("memory_context", []),
        state.get("tool_results", []),
        state.get("progress_callback"),
        llm_time_budget_s=float(state.get("llm_budget_s", 14.0)),
    )
    assessment = FinalAssessment(**specialist_output["assessment"])
    return {
        "messages": [AIMessage(content=specialist_output.get("raw_output", "Crew-based assessment completed."))],
        "assessment": assessment,
        "tool_results": specialist_output.get("tool_results", []),
        "errors": state.get("errors", []),
        "specialist_output": specialist_output,
    }


builder = StateGraph(AuditState)
builder.add_node("crew", crew_node)
builder.add_edge(START, "crew")
builder.add_edge("crew", END)
aegis_graph = builder.compile()


# Documented Nokia NaC sandbox simulator subscribers (tools.py / README). These
# numbers are synthetic demo identities whose "incident history" is an artifact
# of prior demo runs, not evidence. Their audits are still RECORDED so the
# operator history panel shows the demo trail, but their history is excluded
# from verdict WEIGHTING: a clean subscriber (+99999991001) must stay APPROVED
# no matter how many times the demo has been run.
SIMULATOR_MSISDNS = {"+99999991000", "+99999991001", "+99999991002", "+99999991003", "+9999123456"}


def _normalize_tenant(tenant_id: str) -> str:
    """Defensive normalization for the server-derived tenant namespace.

    The tenant is resolved server-side (from the API client's credential via
    main._resolve_tenant), so this is belt-and-suspenders: it keeps the value
    in the shape used for memory scoping and record metadata, and falls back to
    the configured default if anything unexpected ever reaches here.
    """
    value = (tenant_id or "").strip().lower()
    if not value or not all(ch.isalnum() or ch in "-_" for ch in value):
        return settings.AEGISTEL_DEFAULT_TENANT
    return value


async def execute_audit(request: AuditRequest, progress_callback: Any | None = None, tenant_id: str | None = None) -> AuditResponse:
    t0 = time.monotonic()
    # The tenant namespace is supplied by the caller of this function (main.py
    # derives it from the API client's credential). It is never read from the
    # request body — AuditRequest has no tenant field. Everything downstream
    # (memory writes, memory reads, response echo) uses this single value.
    tenant_id = _normalize_tenant(tenant_id or settings.AEGISTEL_DEFAULT_TENANT)
    audit_id = uuid.uuid4()
    def _mark(label: str) -> float:
        return round((time.monotonic() - t0) * 1000, 1)
    timing: Dict[str, float] = {"total_ms": 0.0}
    request_context = {
        "msisdn": request.msisdn,
        "amount": request.amount,
        "latitude": request.current_location.latitude,
        "longitude": request.current_location.longitude,
        "request_qod": request.request_qod_slice,
        "transaction_type": request.transaction_type,
        "tenant_id": tenant_id,
        "metadata": request.metadata,
        "agent_planning": True,
        "force_deterministic": bool(request.metadata.get("_force_deterministic")),
    }
    print(f"[Graph Orchestrator] Request context: {request_context}")
    is_simulator = request.msisdn in SIMULATOR_MSISDNS
    memory_context: List[Dict[str, Any]] = []
    if not is_simulator:
        memory_context = await memory_engine.retrieve_past_incidents_async(
            request.msisdn,
            f"fraud pattern {request.transaction_type} {request.current_location.latitude} {request.current_location.longitude}",
            tenant_id=tenant_id,
        )
    timing["memory_retrieve_ms"] = _mark("memory_retrieve")
    if progress_callback is not None:
        try:
            # Report the full recorded trail (simulator audits are recorded but
            # excluded from verdict weighting, so len(memory_context) would lie
            # to the operator as 0). Weighting semantics are untouched.
            prior_count = len(memory_engine.list_all_incidents(request.msisdn, tenant_id=tenant_id))
            progress_callback({"type": "memory:done", "incidents": prior_count})
        except Exception:
            pass
    print(f"[Memory Engine] Retrieved {len(memory_context)} past incidents for {request.msisdn}"
          + (" (simulator subscriber: memory excluded from verdict weighting)" if is_simulator else ""))

    initial_input: AuditState = {
        "messages": [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"Transaction Context:\n"
                    f"- MSISDN: {request.msisdn}\n"
                    f"- Transaction Type: {request.transaction_type}\n"
                    f"- Amount: ${request.amount:,.2f}\n"
                    f"- Coordinates: Lat {request.current_location.latitude}, Lon {request.current_location.longitude}\n"
                    f"- Request QoD: {request.request_qod_slice}\n"
                    f"- Metadata: {json.dumps(request.metadata, default=str)}"
                )
            ),
        ],
        "assessment": None,
        "memory_context": memory_context,
        "tool_results": [],
        "errors": [],
        "request_context": request_context,
        "specialist_output": {},
        "progress_callback": progress_callback,
        "llm_budget_s": 14.0,
    }
    final_state = await aegis_graph.ainvoke(initial_input)
    timing["crew_ms"] = round((time.monotonic() - t0) * 1000, 1) - timing["memory_retrieve_ms"]
    specialist_output = final_state.get("specialist_output", {}) if isinstance(final_state.get("specialist_output"), dict) else {}
    assessment = FinalAssessment(**final_state["assessment"].model_dump()) if final_state.get("assessment") else FinalAssessment(**specialist_output.get("assessment", {}))

    # QoD is a recommendation only. The decision pipeline never provisions a QoD
    # session (that would borrow a chargeable network resource before any
    # consent). We surface the recommendation to the UI; provisioning happens
    # through the explicit, authenticated, policy-gated confirm endpoint.
    # Recommend QoD for high-risk verdicts that warrant step-up verification.
    qod_recommended = bool(specialist_output.get("qod_recommended")) or assessment.status in {"STEP_UP_REQUIRED", "BLOCKED"}
    if progress_callback is not None:
        try:
            progress_callback(
                {
                    "type": "qod:recommended",
                    "status": assessment.status,
                    "recommended": qod_recommended,
                }
            )
        except Exception:
            pass

    trace: List[AgentTraceItem] = [
        AgentTraceItem(
            agent="Autonomous Orchestrator",
            action="CREW:EXECUTE",
            thought="Executed the specialist workflow for the supplied transaction context.",
            status="EXECUTING",
            detail=json.dumps(request_context, default=str),
        )
    ]

    specialist_trace = [
        AgentTraceItem(
            agent=item["agent"],
            action=item["action"],
            thought=item["thought"],
            status=item["status"],
            detail=item["detail"],
            model=item.get("model"),
            provider=item.get("provider"),
        )
        for item in specialist_output.get("trace", [])
    ]
    if specialist_trace:
        trace.extend(specialist_trace)

    tool_results = final_state.get("tool_results", [])
    evidence_summary = {"live_sdk": 0, "sandbox": 0, "local_fallback": 0}
    for item in tool_results:
        source = str(item.get("source", "")).strip().lower()
        if item.get("error"):
            bucket = "local_fallback"
        elif source == "nokia nac sdk":
            bucket = "live_sdk"
        elif source in {"local fallback", "local_fallback"}:
            bucket = "local_fallback"
        elif "sandbox" in source or "simulator" in source:
            bucket = "sandbox"
        else:
            bucket = "local_fallback"
        evidence_summary[bucket] += 1
    telemetry = NokiaApiTelemetry(
        device_swap_detected=next(
            (item.get("deviceSwapped") or item.get("swapped") for item in tool_results if item.get("deviceSwapped") is not None or item.get("swapped") is not None),
            None,
        ),
        number_recycled=next(
            (item.get("phoneNumberRecycled") for item in tool_results if item.get("phoneNumberRecycled") is not None),
            None,
        ),
        sim_swap_detected=assessment.sim_swap_detected,
        last_sim_swap_date=assessment.last_sim_swap_date,
        location_verification_match=assessment.location_verification_match,
        location_accuracy_meters=assessment.location_accuracy_meters,
        geofence_status=assessment.geofence_status,
        roaming_status=assessment.roaming_status,
        roaming_country=assessment.roaming_country,
        reachability_status=assessment.reachability_status,
        number_verification_match=assessment.number_verification_match,
        number_verification_status=assessment.number_verification_status,
        max_congestion_level=assessment.max_congestion_level,
        qod_session_active=assessment.qod_session_active,
        qod_profile=assessment.qod_profile,
        qod_status=assessment.qod_status,
        tool_results=[
            ToolCallResult(
                name=item.get("name", "tool"),
                success=_tool_succeeded(item),
                source=item.get("source", "sandbox"),
                duration_ms=item.get("duration_ms"),
                payload=item,
            )
            for item in tool_results
        ],
        evidence_strength={"CRITICAL": "CRITICAL", "HIGH": "HIGH", "MEDIUM": "MEDIUM", "LOW": "LOW"}.get(
            assessment.risk_score, "MEDIUM"
        ),
        confidence=_compute_confidence(tool_results),
        cross_border_risk=assessment.roaming_status == "INTERNATIONAL_ROAMING",
        evidence_summary=evidence_summary,
    )
    memory_engine.record_incident(
        request.msisdn,
        f"{request.transaction_type} risk={assessment.risk_score} status={assessment.status}",
        metadata={
            "amount": request.amount,
            "risk_score": assessment.risk_score,
            "status": assessment.status,
            "roaming_status": assessment.roaming_status,
            "tenant_id": tenant_id,
            "audit_id": str(audit_id),
            "qod_recommended": qod_recommended,
        },
    )
    timing["total_ms"] = round((time.monotonic() - t0) * 1000, 1)
    # Quick coherence check: if the textual recommended action clearly contradicts
    # the structured `status`, add a trace item so the UI highlights the mismatch
    rec_lower = (assessment.recommended_action or "").lower()
    if assessment.status == "APPROVED" and any(k in rec_lower for k in ("block", "deny", "human", "review", "escalat")):
        trace.append(
            AgentTraceItem(
                agent="Autonomous Orchestrator",
                action="RECOMMENDATION_COHERENCE_CHECK",
                thought="Detected potential mismatch between `status` and `recommended_action`.",
                status="REVIEWED",
                detail=f"status={assessment.status} | recommended_action={assessment.recommended_action}",
            )
        )
    return AuditResponse(
        audit_id=audit_id,
        msisdn=request.msisdn,
        amount=request.amount,
        transaction_type=request.transaction_type,
        tenant_id=tenant_id,
        risk_score=assessment.risk_score,
        status=assessment.status,
        qod_recommended=qod_recommended,
        telemetry=telemetry,
        reasoning=assessment.reasoning,
        recommended_action=assessment.recommended_action,
        agent_trace=trace,
        used_fallback=specialist_output.get("used_fallback", False),
        raw_output=specialist_output.get("raw_output"),
        diagnostics={
            "timing_ms": timing,
            "phases_ms": specialist_output.get("timing", {}),
            "providers_configured": {
                "groq": bool(settings.GROQ_API_KEY),
                "gemini": bool(settings.GOOGLE_API_KEY),
                "openrouter": bool(settings.OPENROUTER_API_KEY),
            },
            "providers_reachable": specialist_output.get("providers_reachable", {}),
        },
    )
