# backend/app/agents/crew_specialists.py
import asyncio
import json
import logging
import re
import time
import warnings
from typing import Any, Dict, List

from crewai import Agent, Crew, Task

from app.agents.tools import (
    check_device_reachability,
    check_roaming_status,
    check_sim_swap,
    create_qod_session,
    verify_location,
)
from app.core.config import settings
from app.core.constants import ISO_COUNTRY_NAMES

logger = logging.getLogger(__name__)
warnings.filterwarnings("ignore", category=DeprecationWarning)

try:
    import litellm
except Exception:
    litellm = None

# Fast, reliable free-tier models are preferred first. Rate-limited providers
# (e.g. an exhausted Gemini free-tier quota) are skipped via _PROVIDER_COOLDOWN
# so a request never burns a ~26s retry or a ~6.6s native-provider import on a
# model that is known to be unavailable.
MODEL_CHAIN = {
    "specialist": [
        "groq/llama-3.3-70b-versatile",
        "groq/llama-3.1-8b-instant",
        "openrouter/openai/gpt-4o-mini",
        f"gemini/{settings.GEMINI_MODEL}",
    ],
    "auditor": [
        "groq/llama-3.1-8b-instant",
        "openrouter/openai/gpt-4o-mini",
        f"gemini/{settings.GEMINI_MODEL}",
    ],
}

# In-process record of models that recently hit a rate-limit/quota error, and
# the earliest wall-clock timestamp at which retrying them makes sense.
_PROVIDER_COOLDOWN: Dict[str, float] = {}
_PROVIDER_COOLDOWN_WINDOW_S = 60.0

VALID_STATUSES = {"APPROVED", "REJECTED", "BLOCKED", "STEP_UP_REQUIRED", "MANUAL_REVIEW"}
VALID_RISK_SCORES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}

# Explicit transaction types which should be treated as higher-risk signals
HIGH_RISK_TX_TYPES = {"CROSS_BORDER_SWIFT", "SAME_DAY_WIRE", "GIFT_CARD_TOPUP"}


def _patch_litellm_for_crewai() -> None:
    if litellm is None or getattr(litellm, "_aegistel_patched", False):
        return

    original_completion = getattr(litellm, "completion", None)
    if original_completion is None:
        return

    def _safe_completion(*args: Any, **kwargs: Any) -> Any:
        messages = kwargs.get("messages") or []
        cleaned_messages: List[Dict[str, Any]] = []
        for message in messages:
            if isinstance(message, dict):
                clean_message = dict(message)
                clean_message.pop("cache_breakpoint", None)
                cleaned_messages.append(clean_message)
            else:
                cleaned_messages.append(message)
        kwargs["messages"] = cleaned_messages
        return original_completion(*args, **kwargs)

    litellm.completion = _safe_completion
    litellm._aegistel_patched = True


_patch_litellm_for_crewai()


def _find_tool_result(tool_results: List[Dict[str, Any]], *keys: str) -> Dict[str, Any] | None:
    """Return the first tool payload that contains one of the requested key names."""
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        if any(key in item for key in keys):
            return item
    return None


def synthesize_specialist_assessment(
    request_context: Dict[str, Any],
    tool_results: List[Dict[str, Any]],
    memory_context: List[Dict[str, Any]],
    enforce_roaming_policy: bool = False,
) -> Dict[str, Any]:
    """Convert tool output into a grounded multi-agent fraud assessment."""
    msisdn = request_context.get("msisdn", "")
    amount = float(request_context.get("amount", 0.0))
    request_qod = bool(request_context.get("request_qod"))
    enforce_roaming_policy = bool(enforce_roaming_policy)
    transaction_type = str(request_context.get("transaction_type", "")).upper()

    sim_result = _find_tool_result(tool_results, "swapped")
    location_result = _find_tool_result(tool_results, "verificationResult")
    roaming_result = _find_tool_result(tool_results, "roamingStatus")
    reachability_result = _find_tool_result(tool_results, "reachabilityStatus", "reachable")
    qod_result = _find_tool_result(tool_results, "qosStatus", "qosProfile")

    sim_swapped = bool(sim_result and sim_result.get("swapped"))
    verification_result = str(location_result.get("verificationResult", "TRUE")).upper() if location_result else "TRUE"
    verification_match = verification_result == "TRUE"
    geofence_status = {
        "TRUE": "VERIFIED",
        "FALSE": "NOT_VERIFIED",
        "PARTIAL": "PARTIAL",
    }.get(verification_result, "UNKNOWN")
    location_accuracy_meters = float(location_result.get("radius_meters", 120.0)) if location_result and location_result.get("radius_meters") is not None else 120.0
    roaming_status = roaming_result.get("roamingStatus", "DOMESTIC") if roaming_result else "DOMESTIC"
    roaming_country = None
    if roaming_result:
        country_codes = roaming_result.get("countryIsoCodes") or []
        if country_codes:
            roaming_country = ISO_COUNTRY_NAMES.get(country_codes[0], country_codes[0])
    qod_active = bool(qod_result and qod_result.get("qosStatus"))
    qod_profile = qod_result.get("qosProfile") if qod_result else None
    qod_status = qod_result.get("qosStatus") if qod_result else None
    memory_hits = bool(memory_context)
    roaming_policy_violation = enforce_roaming_policy and roaming_status == "INTERNATIONAL_ROAMING"
    reachability_status = reachability_result.get("reachabilityStatus", "UNKNOWN") if reachability_result else "UNKNOWN"
    unreachable_risk = reachability_status == "UNREACHABLE"
    amount_risk = amount >= 100000

    tx_high_risk = transaction_type in HIGH_RISK_TX_TYPES

    trace_items: List[Dict[str, Any]] = []

    security_thought = (
        f"Security Specialist flagged a SIM swap event for {msisdn}."
        if sim_swapped
        else f"Security Specialist found no recent SIM swap evidence for {msisdn}."
    )
    trace_items.append(
        {
            "agent": "Security Specialist",
            "action": "SIM_SWAP_EVALUATION",
            "thought": security_thought,
            "status": "FLAGGED" if sim_swapped else "CLEARED",
            "detail": f"swapped={sim_swapped} | source={sim_result.get('source', 'sandbox') if sim_result else 'unknown'}",
        }
    )

    network_thought = (
        "Network Intelligence Specialist found a location verification mismatch."
        if geofence_status in {"NOT_VERIFIED", "PARTIAL", "UNKNOWN"}
        else "Network Intelligence Specialist confirmed the device location aligned with the request context."
    )
    if roaming_policy_violation:
        network_thought += " The enforced roaming policy requires domestic-only traffic."
    trace_items.append(
        {
            "agent": "Network Intelligence Specialist",
            "action": "LOCATION_AND_ROAMING_EVALUATION",
            "thought": network_thought,
            "status": "FLAGGED" if not verification_match or roaming_policy_violation else "CLEARED",
            "detail": (
                f"verificationResult={'TRUE' if verification_match else 'FALSE'} | "
                f"roaming={roaming_status} | reachability={reachability_result.get('reachabilityStatus', 'UNKNOWN') if reachability_result else 'UNKNOWN'}"
            ),
        }
    )

    # Transaction-type evaluator: certain transaction types are considered
    # higher risk by policy. They provide deterministic corroborating
    # context similar to roaming but are not as severe as a SIM swap.
    trace_items.append(
        {
            "agent": "Risk Type Evaluator",
            "action": "TRANSACTION_TYPE_EVALUATION",
            "thought": (
                f"Transaction type for request is '{transaction_type}'."
                if transaction_type
                else "Transaction type unspecified."
            ),
            "status": "FLAGGED" if tx_high_risk else "CLEARED",
            "detail": f"transaction_type={transaction_type}",
        }
    )

    # Memory is exploratory context only; it should not by itself flip the
    # deterministic risk signal. Record the presence of prior incidents as a
    # trace-only item so operators can see corroborating history without
    # automatically escalating verdicts.
    risk_signal = (
        sim_swapped
        or verification_result in {"FALSE", "PARTIAL", "UNKNOWN"}
        or roaming_status == "INTERNATIONAL_ROAMING"
        or unreachable_risk
        or tx_high_risk
    )
    if memory_hits:
        trace_items.append(
            {
                "agent": "Memory Agent",
                "action": "RECURRENCE_EVIDENCE",
                "thought": f"Found {len(memory_context)} prior incident(s) for {msisdn}; recorded as corroborating context only.",
                "status": "FOUND",
                "detail": f"memory_count={len(memory_context)}",
            }
        )
    if roaming_policy_violation:
        risk_signal = True

    if amount_risk and not risk_signal:
        status = "STEP_UP_REQUIRED"
        risk_score = "MEDIUM"
        reasoning = (
            f"Specialist synthesis found no compromise indicators, but the transaction amount of ${amount:,.2f} "
            "exceeds the standard auto-approval threshold, warranting step-up verification regardless."
        )
        recommended_action = "Request additional verification before final approval given transaction size."
    elif risk_signal or amount_risk:
        status = "STEP_UP_REQUIRED"
        risk_score = "CRITICAL" if risk_signal and amount_risk else ("HIGH" if risk_signal else "MEDIUM")
        parts = []
        if sim_swapped:
            parts.append("SIM swap evidence was present.")
        if not verification_match:
            parts.append("The location verification did not match the expected network context.")
        if roaming_status == "INTERNATIONAL_ROAMING":
            parts.append("The subscriber was observed on an international roaming context.")
        if unreachable_risk:
            parts.append("The device was unreachable, preventing secondary verification.")
        if roaming_policy_violation:
            parts.append("The transaction violated the enforced roaming policy by using international roaming.")
        if memory_hits:
            parts.append("Historical fraud memory added corroborating context.")
        if amount_risk:
            parts.append(f"The transaction amount of ${amount:,.2f} exceeded the standard auto-approval threshold.")
        if amount >= 25000 or request_qod:
            parts.append("A QoD-assisted step-up was recommended for the transaction.")
        if tx_high_risk:
            parts.append(f"The transaction type '{transaction_type}' is classified as high risk by policy.")
        # Defensive: ensure reasoning is meaningful even if parts is empty
        if parts:
            reasoning = "Specialist synthesis identified: " + " ".join(parts)
        else:
            reasoning = (
                "Specialist synthesis identified no direct compromise indicators, "
                "but the transaction context warrants further review."
            )
        recommended_action = "Escalate the payment with a QoD-assisted step-up and human review."
    else:
        status = "APPROVED"
        risk_score = "LOW"
        reasoning = "Specialist synthesis found no strong compromise indicators and approved the transaction for the supplied network context."
        recommended_action = "Allow the transaction and continue monitoring for additional telemetry."

    trace_items.append(
        {
            "agent": "Risk Auditor",
            "action": "DECISION_SYNTHESIS",
            "thought": reasoning,
            "status": status,
            "detail": f"risk_score={risk_score} | status={status}",
        }
    )

    return {
        "assessment": {
            "status": status,
            "risk_score": risk_score,
            "sim_swap_detected": sim_swapped,
            "last_sim_swap_date": sim_result.get("last_sim_swap_date") if sim_result else None,
            "location_verification_match": verification_match,
            "location_accuracy_meters": location_accuracy_meters,
            "geofence_status": geofence_status,
            "roaming_status": roaming_status,
            "roaming_country": roaming_country,
            "reachability_status": reachability_result.get("reachabilityStatus", "UNKNOWN") if reachability_result else "UNKNOWN",
            "qod_session_active": qod_active,
            "qod_profile": qod_profile,
                "qod_status": qod_status,
            "reasoning": reasoning,
            "recommended_action": recommended_action,
        },
        "trace": trace_items,
    }


def _parse_structured_output(raw_output: Any) -> Dict[str, Any]:
    if isinstance(raw_output, dict):
        return raw_output
    if isinstance(raw_output, str):
        try:
            return json.loads(raw_output)
        except Exception:
            try:
                return json.loads(raw_output.split("```json", 1)[1].split("```", 1)[0])
            except Exception:
                return {}
    return {}


def _run_tool_payload(tool_name: str, tool_callable: Any, **kwargs: Any) -> Dict[str, Any]:
    try:
        raw_result = tool_callable.run(**kwargs) if hasattr(tool_callable, "run") else tool_callable(**kwargs)
        parsed_result = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
        if isinstance(parsed_result, dict):
            parsed_result = dict(parsed_result)
            parsed_result.setdefault("name", tool_name)
            return parsed_result
    except Exception as exc:
        logger.debug("Tool %s failed: %s", tool_name, exc)
    return {"name": tool_name, "status_code": 200, "source": "sandbox", "error": "tool execution failed"}


def _pick_value(parsed_output: Dict[str, Any], deterministic_output: Dict[str, Any], key: str) -> Any:
    if key in parsed_output and parsed_output[key] is not None:
        value = parsed_output[key]
        if key == "status" and value in VALID_STATUSES:
            return value
        if key == "risk_score" and value in VALID_RISK_SCORES:
            return value
        if key in {"sim_swap_detected", "location_verification_match", "qod_session_active"}:
            return deterministic_output["assessment"][key]
        if key in {"qod_profile", "roaming_country", "recommended_action", "reasoning", "last_sim_swap_date"} and isinstance(value, (str, type(None))):
            return value
        if key in {"roaming_status", "reachability_status", "geofence_status"} and isinstance(value, str):
            return value
        if key == "location_accuracy_meters" and isinstance(value, (int, float)):
            return float(value)
        if key in {"amount"}:
            return value
        if key in {"qod_session_active"}:
            return bool(value)
        return deterministic_output["assessment"][key]
    return deterministic_output["assessment"][key]


def _check_prose_against_grounded_fields(reasoning: str, assessment: Dict[str, Any]) -> tuple[str, List[str]]:
    issues: List[str] = []
    if not reasoning:
        return reasoning, issues

    roaming_status = assessment.get("roaming_status")
    if roaming_status:
        roaming_status_token_re = re.compile(r"\b([A-Z][A-Z0-9_]+(?:_[A-Z0-9_]+)*)\b")
        roaming_claim = roaming_status_token_re.search(reasoning)
        if roaming_claim and roaming_claim.group(1).upper() != roaming_status:
            candidate = roaming_claim.group(1).upper()
            if "ROAMING" in candidate and candidate != roaming_status:
                reasoning = reasoning.replace(candidate, roaming_status)
                issues.append(
                    f"Prose claimed roaming_status='{candidate}' but grounded value is '{roaming_status}'."
                )

        roaming_phrase = re.search(r"roaming status (?:being|is|of)\s+['\"]?([A-Z_]+)['\"]?", reasoning, re.IGNORECASE)
        if roaming_phrase and roaming_phrase.group(1).upper() != roaming_status:
            reasoning = reasoning.replace(
                roaming_phrase.group(0),
                f"roaming status being '{roaming_status}'",
            )
            issues.append(
                f"Prose claimed roaming_status='{roaming_phrase.group(1)}' but grounded value is '{roaming_status}'."
            )

    return reasoning, issues


def _reconcile_crew_output(parsed_output: Dict[str, Any], deterministic_output: Dict[str, Any]) -> tuple[Dict[str, Any], List[str]]:
    if not isinstance(parsed_output, dict) or not parsed_output:
        return deterministic_output["assessment"], ["CrewAI did not return a parseable structured JSON assessment."]

    final_assessment: Dict[str, Any] = {}
    mismatch_reasons: List[str] = []

    for key in deterministic_output["assessment"]:
        model_has_key = key in parsed_output
        model_value = parsed_output.get(key)
        chosen = _pick_value(parsed_output, deterministic_output, key)
        final_assessment[key] = chosen

        if model_has_key and model_value is not None and chosen == deterministic_output["assessment"][key] and model_value != deterministic_output["assessment"][key]:
            mismatch_reasons.append(
                f"Field '{key}' contained an untrusted CrewAI value {model_value!r}; using deterministic grounding."
            )

    # Enforce the non-downgrade floor: the CrewAI output may be stricter than
    # deterministic grounding, but it must not be more lenient. If the
    # deterministic status is not APPROVED, do not allow CrewAI to set status
    # to APPROVED. Similarly, do not allow the risk_score to be lowered below
    # the deterministic level. Record attempts to downgrade in mismatch_reasons.
    det_status = deterministic_output["assessment"].get("status")
    det_risk = deterministic_output["assessment"].get("risk_score")
    severity = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    if det_status and det_status != "APPROVED":
        if final_assessment.get("status") == "APPROVED":
            mismatch_reasons.append(
                f"CrewAI attempted to downgrade deterministic status '{det_status}' to 'APPROVED'; preserving deterministic floor."
            )
            final_assessment["status"] = det_status
    # Risk score floor
    if det_risk and final_assessment.get("risk_score"):
        det_val = severity.get(det_risk, 2)
        model_val = severity.get(final_assessment.get("risk_score"), 2)
        if model_val < det_val:
            mismatch_reasons.append(
                f"CrewAI attempted to lower risk_score from '{det_risk}' to '{final_assessment.get('risk_score')}'. Preserving deterministic risk '{det_risk}'."
            )
            final_assessment["risk_score"] = det_risk

    # Enforce the no-escalation ceiling on clean cases: when the deterministic
    # engine found no grounded risk signal (status APPROVED), the CrewAI output
    # must not invent risk. The LLM may INTENSIFY confirmed risk (e.g. turn a
    # deterministic STEP_UP_REQUIRED into REJECTED), but it must never escalate
    # a clean case — otherwise the same transaction would return different
    # verdicts depending on whether the LLM fallback path was active.
    escalation_capped = False
    if det_status == "APPROVED":
        model_status = final_assessment.get("status")
        if model_status != "APPROVED":
            mismatch_reasons.append(
                f"CrewAI attempted to escalate deterministic status 'APPROVED' to '{model_status}' with no grounded risk signal; preserving APPROVED."
            )
            final_assessment["status"] = "APPROVED"
            escalation_capped = True
            # Keep the final judgment text coherent with the forced APPROVED
            # verdict instead of echoing a hallucinated escalation.
            final_assessment["reasoning"] = deterministic_output["assessment"].get("reasoning")
            final_assessment["recommended_action"] = deterministic_output["assessment"].get("recommended_action")
        model_risk = final_assessment.get("risk_score")
        if model_risk and model_risk != "LOW":
            mismatch_reasons.append(
                f"CrewAI attempted to raise risk_score from 'LOW' to '{model_risk}' on a clean case; preserving LOW."
            )
            final_assessment["risk_score"] = "LOW"

    # Prose-level validation: if the model produced free-text reasoning that
    # mentions a specific country while the structured `roaming_country` is
    # absent, sanitize the prose and record the correction. We also sanitize
    # invented status values such as "DOMESTIC_ROAMING" if the grounded field
    # clearly differs.
    # When the escalation ceiling forced an APPROVED verdict on a clean case,
    # the deterministic reasoning was already substituted; skip prose
    # re-processing of the model's hallucinated text.
    reasoning_raw = parsed_output.get("reasoning") or deterministic_output["assessment"].get("reasoning")
    if escalation_capped:
        reasoning_raw = None
    if reasoning_raw:
        sanitized_reasoning, prose_issues = _check_prose_against_grounded_fields(reasoning_raw, final_assessment)
        if not final_assessment.get("roaming_country"):
            country_claim_re = re.compile(r"\b(?:in|located in|country of|from)\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)*)")
            matches = country_claim_re.findall(sanitized_reasoning)
            if matches:
                sanitized_reasoning = country_claim_re.sub("in an unspecified country", sanitized_reasoning)
                prose_issues.append(
                    f"Prose claimed specific country names {matches!r} while structured roaming_country was absent; sanitized reasoning."
                )
        final_assessment["reasoning"] = sanitized_reasoning
        mismatch_reasons.extend(prose_issues)
    elif not escalation_capped:
        final_assessment["reasoning"] = reasoning_raw

    return final_assessment, mismatch_reasons


def _is_rate_limit_or_availability_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in ("429", "rate_limit", "quota", "no longer available", "temporarily unavailable", "service unavailable", "overloaded"))


def _is_crew_tool_validation_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "tool call validation failed" in text or "attempted to call tool" in text or "invalid_request_error" in text


def _model_provider_available(model: str) -> bool:
    if model.startswith("groq/"):
        return bool(settings.GROQ_API_KEY)
    if model.startswith("cerebras/"):
        return bool(settings.CEREBRAS_API_KEY)
    if model.startswith("gemini/"):
        return bool(settings.GOOGLE_API_KEY)
    if model.startswith("openrouter/"):
        return bool(settings.OPENROUTER_API_KEY)
    return False


def _model_in_cooldown(model: str) -> bool:
    expiry = _PROVIDER_COOLDOWN.get(model, 0.0)
    if expiry > 0.0 and expiry > time.monotonic():
        return True
    if expiry > 0.0 and expiry <= time.monotonic():
        _PROVIDER_COOLDOWN.pop(model, None)
    return False


def _mark_model_cooldown(model: str) -> None:
    _PROVIDER_COOLDOWN[model] = time.monotonic() + _PROVIDER_COOLDOWN_WINDOW_S


def _model_provider_name(model: str) -> str:
    if model.startswith("groq/"):
        return "Groq"
    if model.startswith("cerebras/"):
        return "Cerebras"
    if model.startswith("gemini/"):
        return "Gemini"
    if model.startswith("openrouter/"):
        return "OpenRouter"
    return "Unknown"


def _build_task_description(
    role: str,
    executed_tool_results: List[Dict[str, Any]],
    memory_context: List[Dict[str, Any]],
    msisdn: str,
    amount: float,
    request_qod: bool,
    enforce_roaming_policy: bool = False,
    transaction_type: str | None = None,
) -> str:
    if role == "security":
        evidence = [item for item in executed_tool_results if item.get("name") in {"check_sim_swap", "check_roaming_status"}]
    elif role == "network":
        evidence = [item for item in executed_tool_results if item.get("name") in {"verify_location", "check_device_reachability", "create_qod_session"}]
    else:
        evidence = [item for item in executed_tool_results if item.get("name") in {"check_sim_swap", "check_roaming_status", "verify_location", "check_device_reachability", "create_qod_session"}]

    memory_summary = ", ".join(
        f"{item.get('text', 'memory')}" for item in memory_context[:3]
    ) or "no prior incidents"
    evidence_json = json.dumps(evidence, default=str, separators=(",", ":"))
    memory_json = json.dumps(memory_context[:3], default=str, separators=(",", ":"))

    qod_note = ""
    if role == "risk":
        qod_session = next((item for item in executed_tool_results if item.get("name") == "create_qod_session" and item.get("sessionId")), None)
        if qod_session:
            qod_note = f" A QoD session with id {qod_session.get('sessionId')} has already been created for this request; do not create another."
        large_amount = amount >= 100000
        unreachable = any(
            item.get("name") == "check_device_reachability" and item.get("reachabilityStatus") == "UNREACHABLE"
            for item in executed_tool_results
        )
        if large_amount or unreachable:
            qod_note += " Step-up decisions should treat large amounts and unreachable devices as meaningful risk signals; do not approve automatically on those conditions alone."

        if transaction_type:
            qod_note += f" Transaction type context: {str(transaction_type).upper()}."

    roaming_policy_note = (
        " Enforce the roaming policy: treat international roaming as a violation and prioritize domestic traffic."
        if enforce_roaming_policy
        else ""
    )

    if role == "security":
        return (
            f"Investigate the transaction for {msisdn} using only the relevant security evidence. "
            f"Transaction amount is ${amount:,.2f} and QoD requested is {request_qod}. "
            f"Evidence JSON: {evidence_json}. Memory context: {memory_summary}.{roaming_policy_note}"
        )
    if role == "network":
        return (
            f"Validate the network context for {msisdn} using only the relevant connectivity evidence. "
            f"Transaction amount is ${amount:,.2f} and QoD requested is {request_qod}. "
            f"Evidence JSON: {evidence_json}. Memory context: {memory_summary}.{roaming_policy_note}"
        )
    return (
        f"Review the specialist outputs for {msisdn} and decide the final fraud verdict. "
        f"Use only the concise evidence summary below. Transaction amount is ${amount:,.2f}. "
        f"Evidence JSON: {evidence_json}. Memory context: {memory_json}."
        f"{qod_note}"
        f"{roaming_policy_note}"
        "\n\nNote: `status` must be exactly one of: APPROVED, REJECTED, BLOCKED, STEP_UP_REQUIRED, MANUAL_REVIEW."
        "\nNote: `risk_score` must be exactly one of: LOW, MEDIUM, HIGH, CRITICAL. Do not supply numeric scores."
        "\nBase your reasoning strictly on the evidence JSON provided. Do not state a specific country, date, or other detail unless it appears verbatim in the evidence."
        " If a field like roaming_country is null or absent, say the country is unknown or unspecified — do not infer or invent one."
    )


def _log_prompt_size(description: str, label: str) -> None:
    estimated_tokens = max(1, len(description) // 4)
    if estimated_tokens > 3000:
        logger.warning("Large %s prompt detected: approx %s tokens", label, estimated_tokens)


def run_specialist_crew(
    request_context: Dict[str, Any],
    memory_context: List[Dict[str, Any]],
    tool_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Run a real CrewAI specialist workflow and fall back to deterministic synthesis on errors."""
    msisdn = request_context.get("msisdn", "")
    amount = float(request_context.get("amount", 0.0))
    longitude = float(request_context.get("longitude", 46.7))
    latitude = float(request_context.get("latitude", 24.7))
    request_qod = bool(request_context.get("request_qod"))

    transaction_type = str(request_context.get("transaction_type", "")).upper()

    metadata = request_context.get("metadata") or {}
    try:
        geofence_radius_meters = int(float(metadata.get("geofence_radius_meters", 5000)))
    except (TypeError, ValueError):
        geofence_radius_meters = 5000
    if geofence_radius_meters <= 0:
        geofence_radius_meters = 5000
    enforce_roaming_policy = bool(metadata.get("enforce_roaming_policy"))

    from concurrent.futures import ThreadPoolExecutor

    # The telemetry tools are independent SDK calls. Running them concurrently
    # removes the serial latency without changing which tools run.
    jobs: List[tuple[str, Any]] = [
        ("check_sim_swap", lambda: _run_tool_payload("check_sim_swap", check_sim_swap, msisdn=msisdn)),
        (
            "verify_location",
            lambda: _run_tool_payload(
                "verify_location",
                verify_location,
                msisdn=msisdn,
                latitude=latitude,
                longitude=longitude,
                radius=geofence_radius_meters,
            ),
        ),
        ("check_roaming_status", lambda: _run_tool_payload("check_roaming_status", check_roaming_status, msisdn=msisdn)),
        ("check_device_reachability", lambda: _run_tool_payload("check_device_reachability", check_device_reachability, msisdn=msisdn)),
    ]
    if amount >= 25000 or request_qod:
        jobs.append(("create_qod_session", lambda: _run_tool_payload("create_qod_session", create_qod_session, msisdn=msisdn)))

    logger.info("verify_location called with radius=%s for msisdn=%s", geofence_radius_meters, msisdn)
    with ThreadPoolExecutor(max_workers=len(jobs)) as _pool:
        _futures = [(_name, _pool.submit(_fn)) for _name, _fn in jobs]
        executed_tool_results = [(_fut[1].result()) for _fut in _futures]

    deterministic_output = synthesize_specialist_assessment(
        request_context,
        executed_tool_results,
        memory_context,
        enforce_roaming_policy=enforce_roaming_policy,
    )
    fallback_trace = deterministic_output.get("trace", [])
    failure_reason = "CrewAI not executed"

    provider_available = any(
        [
            settings.GROQ_API_KEY,
            settings.GOOGLE_API_KEY,
            settings.OPENROUTER_API_KEY,
        ]
    )
    if not provider_available:
        logger.warning("No provider credentials available; using deterministic specialist fallback for crew workflow")
        return {
            "assessment": deterministic_output["assessment"],
            "tool_results": executed_tool_results,
            "trace": fallback_trace,
            "raw_output": "Deterministic fallback used because no provider credentials were configured.",
            "used_fallback": True,
        }

    supported_specialist_models = [
        model for model in MODEL_CHAIN["specialist"]
        if _model_provider_available(model) and not _model_in_cooldown(model)
    ]
    supported_auditor_models = [
        model for model in MODEL_CHAIN["auditor"]
        if _model_provider_available(model) and not _model_in_cooldown(model)
    ]
    if not supported_specialist_models:
        # Every configured model is in cooldown (all rate-limited). Lift the
        # cooldown so a provider can serve this request instead of failing
        # straight to the deterministic fallback.
        _PROVIDER_COOLDOWN.clear()
        supported_specialist_models = [model for model in MODEL_CHAIN["specialist"] if _model_provider_available(model)]
        supported_auditor_models = [model for model in MODEL_CHAIN["auditor"] if _model_provider_available(model)]
    if not supported_specialist_models or not supported_auditor_models:
        logger.warning("Insufficient provider-backed models for specialist/auditor workflow; using deterministic specialist fallback")
        return {
            "assessment": deterministic_output["assessment"],
            "tool_results": executed_tool_results,
            "trace": fallback_trace,
            "raw_output": "Deterministic fallback used because the specialist/auditor model chain could not be built.",
            "used_fallback": True,
        }

    fallback_model_pairs: List[tuple[str, str]] = []
    for idx in range(max(len(supported_specialist_models), len(supported_auditor_models))):
        specialist_model = supported_specialist_models[idx] if idx < len(supported_specialist_models) else supported_specialist_models[-1]
        auditor_model = supported_auditor_models[idx] if idx < len(supported_auditor_models) else supported_auditor_models[-1]
        if (specialist_model, auditor_model) not in fallback_model_pairs:
            fallback_model_pairs.append((specialist_model, auditor_model))

    if not fallback_model_pairs:
        logger.warning("No configured provider-backed models available; using deterministic specialist fallback")
        return {
            "assessment": deterministic_output["assessment"],
            "tool_results": executed_tool_results,
            "trace": fallback_trace,
            "raw_output": "Deterministic fallback used because no configured provider-backed models were available.",
            "used_fallback": True,
        }
    last_error: Exception | None = None

    def _try_crew_run(specialist_model: str, auditor_model: str, model_index: int) -> Dict[str, Any] | None:
        nonlocal last_error
        try:
            security_agent = Agent(
                role="Security Specialist",
                goal="Identify SIM swap and roaming-based fraud signals with evidence from the telecom tooling layer.",
                backstory="You are a fraud-focused security specialist that investigates SIM swap and identity fraud patterns for high-value telecom transactions.",
                llm=specialist_model,
                verbose=False,
                allow_delegation=False,
            )
            network_agent = Agent(
                role="Network Intelligence Specialist",
                goal="Validate location, reachability, and geofence context for the transaction.",
                backstory="You are a telecom network analyst focused on location, geofence, and reachability signals for fraud investigations.",
                llm=specialist_model,
                verbose=False,
                allow_delegation=False,
            )
            auditor_agent = Agent(
                role="Risk Auditor",
                goal="Synthesize the specialist findings into a final fraud decision and decide if QoD escalation is needed.",
                backstory="You are the final risk auditor. You synthesize the specialist evidence and choose whether a step-up or human review is warranted.",
                llm=auditor_model,
                verbose=False,
                allow_delegation=False,
            )

            security_description = _build_task_description(
                role="security",
                executed_tool_results=executed_tool_results,
                memory_context=memory_context,
                msisdn=msisdn,
                amount=amount,
                request_qod=request_qod,
                enforce_roaming_policy=enforce_roaming_policy,
                transaction_type=transaction_type,
            )
            network_description = _build_task_description(
                role="network",
                executed_tool_results=executed_tool_results,
                memory_context=memory_context,
                msisdn=msisdn,
                amount=amount,
                request_qod=request_qod,
                enforce_roaming_policy=enforce_roaming_policy,
                transaction_type=transaction_type,
            )
            risk_description = _build_task_description(
                role="risk",
                executed_tool_results=executed_tool_results,
                memory_context=memory_context,
                msisdn=msisdn,
                amount=amount,
                request_qod=request_qod,
                enforce_roaming_policy=enforce_roaming_policy,
                transaction_type=transaction_type,
            )
            _log_prompt_size(security_description, "security")
            _log_prompt_size(network_description, "network")
            _log_prompt_size(risk_description, "risk")

            security_task = Task(
                description=security_description,
                expected_output="A JSON object with keys status, evidence, and reasoning.",
                agent=security_agent,
            )
            network_task = Task(
                description=network_description,
                expected_output="A JSON object with keys status, evidence, and reasoning.",
                agent=network_agent,
                context=[security_task],
            )
            risk_task = Task(
                description=risk_description,
                expected_output="A JSON object with keys status, risk_score, reasoning, recommended_action, and qod_session_active.",
                agent=auditor_agent,
            )

            crew = Crew(agents=[security_agent, network_agent, auditor_agent], tasks=[security_task, network_task, risk_task], verbose=False)
            crew_output = crew.kickoff()
            parsed_output = _parse_structured_output(str(crew_output))
            failure_reason = None

            if parsed_output:
                assessment, mismatch_reasons = _reconcile_crew_output(parsed_output, deterministic_output)
                trace = [
                    {
                        "agent": "Security Specialist",
                        "action": "CREWAI_SECURITY_REVIEW",
                        "thought": "CrewAI security specialist executed a model-backed risk review for the provided MSISDN.",
                        "status": "EXECUTED",
                        "model": specialist_model,
                        "provider": _model_provider_name(specialist_model),
                        "detail": f"Model={specialist_model} | Provider={_model_provider_name(specialist_model)} | tier={model_index + 1}",
                    },
                    {
                        "agent": "Network Intelligence Specialist",
                        "action": "CREWAI_NETWORK_REVIEW",
                        "thought": "CrewAI network specialist executed a model-backed location and reachability review.",
                        "status": "EXECUTED",
                        "model": specialist_model,
                        "provider": _model_provider_name(specialist_model),
                        "detail": f"Model={specialist_model} | Provider={_model_provider_name(specialist_model)} | tier={model_index + 1}",
                    },
                    {
                        "agent": "Risk Auditor",
                        "action": "CREWAI_RISK_SYNTHESIS",
                        "thought": "CrewAI risk auditor executed a model-backed reasoning pass and synthesized the final verdict.",
                        "status": "EXECUTED",
                        "model": auditor_model,
                        "provider": _model_provider_name(auditor_model),
                        "detail": f"Model={auditor_model} | Provider={_model_provider_name(auditor_model)} | tier={model_index + 1}",
                    },
                ]
                if mismatch_reasons:
                    trace.append(
                        {
                            "agent": "Risk Auditor",
                            "action": "CREWAI_OUTPUT_VALIDATION",
                            "thought": "CrewAI structured output was validated against deterministic grounding.",
                            "status": "REVIEWED",
                            "detail": "; ".join(mismatch_reasons),
                        }
                    )
                return {
                    "assessment": assessment,
                    "tool_results": executed_tool_results,
                    "trace": trace,
                    "raw_output": str(crew_output),
                    "used_fallback": False,
                }
        except Exception as exc:
            last_error = exc
            if _is_rate_limit_or_availability_error(exc) or _is_crew_tool_validation_error(exc):
                if _is_rate_limit_or_availability_error(exc):
                    _mark_model_cooldown(specialist_model)
                    _mark_model_cooldown(auditor_model)
                logger.warning("CrewAI model chain hit retryable/fallback-worthy error with %s/%s: %s", specialist_model, auditor_model, exc)
            else:
                logger.warning("CrewAI specialist workflow failed unexpectedly for %s/%s; trying next model pair: %s", specialist_model, auditor_model, exc)
            # Every failure moves to the next model in the chain. A dead/missing
            # model should be the least catastrophic outcome, not a request crash.
            return None

    for model_index, (specialist_model, auditor_model) in enumerate(fallback_model_pairs):
        crew_run = _try_crew_run(specialist_model, auditor_model, model_index)
        if crew_run is not None:
            return crew_run

    if last_error is not None:
        failure_reason = str(last_error)
    logger.warning("CrewAI specialist workflow exhausted all fallback models; using deterministic fallback: %s", failure_reason)

    return {
        "assessment": deterministic_output["assessment"],
        "tool_results": executed_tool_results,
        "trace": fallback_trace,
        "raw_output": f"Deterministic fallback used after CrewAI failure: {failure_reason}",
        "used_fallback": True,
    }
