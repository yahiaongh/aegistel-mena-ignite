# backend/app/agents/crew_specialists.py
import json
import logging
import re
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

from crewai import Agent, Crew, Task

from app.agents.tools import (
    check_device_reachability,
    check_roaming_status,
    check_sim_swap,
    create_qod_session,
    get_congestion_insights,
    verify_location,
    verify_number,
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
#
# Groq deprecated llama-3.3-70b-versatile and llama-3.1-8b-instant on
# 2026-08-16; the chain now uses the recommended GPT-OSS family plus
# Qwen3.6-27B (see GroqCloud model list):
#   groq/openai/gpt-oss-120b        ~$0.15/1M in, 250K TPM (quality primary)
#   groq/qwen/qwen3.6-27b           ~$0.60/1M in (second Groq tier)
#   groq/openai/gpt-oss-20b         ~$0.075/1M in, 1000 tps (volume tier)
#   gemini/gemini-3.5-flash-lite    ~1,000 req/day (per-model bucket,
#                                   independent of the flash model's ~20/day)
#   gemini/<configured flash>       quality tier, tightest daily cap
# Cooldowns honor each provider's own retry hint, and daily-quota errors put
# the model out of play until the nightly reset instead of retrying every 60s.
_GEMINI_HIGH_HEADROOM_MODEL = "gemini-3.5-flash-lite"
MODEL_CHAIN = {
    "specialist": [
        "groq/openai/gpt-oss-120b",
        "groq/qwen/qwen3.6-27b",
        "groq/openai/gpt-oss-20b",
        "openrouter/openai/gpt-4o-mini",
        f"gemini/{_GEMINI_HIGH_HEADROOM_MODEL}",
        f"gemini/{settings.GEMINI_MODEL}",
    ],
    "auditor": [
        "groq/openai/gpt-oss-20b",
        "openrouter/openai/gpt-4o-mini",
        f"gemini/{_GEMINI_HIGH_HEADROOM_MODEL}",
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
    number_verification_result = _find_tool_result(tool_results, "devicePhoneNumberVerified", "verificationStatus")
    congestion_result = _find_tool_result(tool_results, "maxCongestionLevel", "congestionLevels")

    sim_swapped = bool(sim_result and sim_result.get("swapped"))
    number_verified = None
    if number_verification_result is not None:
        verified_value = number_verification_result.get("verified")
        if verified_value is None:
            verified_value = number_verification_result.get("devicePhoneNumberVerified")
        if verified_value is not None:
            number_verified = bool(verified_value)
        elif str(number_verification_result.get("verificationStatus", "")).upper() == "VERIFIED":
            number_verified = True
        elif str(number_verification_result.get("verificationStatus", "")).upper() == "FAILED":
            number_verified = False
    number_verification_status = (
        "VERIFIED" if number_verified is True else ("FAILED" if number_verified is False else "UNKNOWN")
    )
    number_verification_risk = number_verified is False or (
        number_verification_status == "UNKNOWN" and number_verification_result is not None
    )

    max_congestion_level = str(congestion_result.get("maxCongestionLevel", "")).lower() if congestion_result else ""
    congestion_high_risk = max_congestion_level == "high"
    congestion_medium = max_congestion_level == "medium"
    verification_result = str(location_result.get("verificationResult", "TRUE")).upper() if location_result else "TRUE"
    if verification_result not in {"TRUE", "FALSE", "PARTIAL", "UNKNOWN"}:
        # A failed/absent verification (e.g. a CAMARA error row yielding an
        # empty result) means the location could not be confirmed. Treat it as
        # UNKNOWN so the transaction is never silently approved on missing data.
        verification_result = "UNKNOWN"
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

    # Memory context is weighted into the verdict: prior incident history for
    # the subscriber corroborates the current transaction. A clean current
    # signal with history still warrants step-up verification, and an already
    # active risk signal is escalated one severity level when history exists.
    high_severity_history = any(
        str((entry.get("metadata") or {}).get("risk_score", "")).upper() in {"HIGH", "CRITICAL"}
        or str((entry.get("metadata") or {}).get("status", "")).upper()
        in {"REJECTED", "BLOCKED", "MANUAL_REVIEW"}
        for entry in (memory_context or [])
    )

    # Number Verification is a silent ownership check: a FAILED or UNKNOWN
    # result means the presented number could not be bound to the device, which
    # is a direct account-takeover signal on the same footing as SIM swap.
    trace_items.append(
        {
            "agent": "Identity Verification Specialist",
            "action": "NUMBER_VERIFICATION_EVALUATION",
            "thought": (
                f"Number Verification returned {number_verification_status} for {msisdn}."
            ),
            "status": "FLAGGED" if number_verification_risk else "CLEARED",
            "detail": f"verificationStatus={number_verification_status} | source={number_verification_result.get('source', 'unknown') if number_verification_result else 'absent'}",
        }
    )

    # Congestion Insights is a contextual signal: sustained High congestion in
    # the subscriber's serving cell corroborates crowd-gathering scenarios
    # (smart cities / mega-events) but is not evidence of fraud on its own.
    trace_items.append(
        {
            "agent": "Congestion Intelligence Specialist",
            "action": "CONGESTION_INSIGHTS_EVALUATION",
            "thought": (
                f"Cell congestion around {msisdn} is {max_congestion_level.upper() or 'UNKNOWN'} over the lookback window."
            ),
            "status": "FLAGGED" if congestion_high_risk else "CLEARED",
            "detail": f"maxCongestionLevel={max_congestion_level or 'absent'} | source={congestion_result.get('source', 'unknown') if congestion_result else 'absent'}",
        }
    )

    risk_signal = (
        sim_swapped
        or verification_result in {"FALSE", "PARTIAL", "UNKNOWN"}
        or roaming_status == "INTERNATIONAL_ROAMING"
        or unreachable_risk
        or tx_high_risk
        or number_verification_risk
    )
    if memory_hits:
        trace_items.append(
            {
                "agent": "Memory Agent",
                "action": "RECURRENCE_EVIDENCE",
                "thought": (
                    f"Found {len(memory_context)} prior incident(s) for {msisdn}; "
                    "history corroborates the current assessment and is weighted into the verdict."
                ),
                "status": "FLAGGED",
                "detail": f"memory_count={len(memory_context)} | high_severity_history={high_severity_history}",
            }
        )
    if roaming_policy_violation:
        risk_signal = True

    if amount_risk and not risk_signal and not memory_hits and not congestion_high_risk:
        status = "STEP_UP_REQUIRED"
        risk_score = "MEDIUM"
        reasoning = (
            f"Specialist synthesis found no compromise indicators, but the transaction amount of ${amount:,.2f} "
            "exceeds the standard auto-approval threshold, warranting step-up verification regardless."
        )
        recommended_action = "Request additional verification before final approval given transaction size."
    elif risk_signal or amount_risk or memory_hits:
        status = "STEP_UP_REQUIRED"
        if risk_signal and amount_risk:
            risk_score = "CRITICAL"
        elif risk_signal:
            risk_score = "HIGH"
        else:
            risk_score = "HIGH" if high_severity_history else "MEDIUM"
        if memory_hits and (risk_signal or amount_risk) and risk_score != "CRITICAL":
            risk_score = "CRITICAL" if risk_score == "HIGH" else "HIGH"
        if congestion_high_risk and (risk_signal or amount_risk or memory_hits) and risk_score != "CRITICAL":
            # Congestion is contextual corroboration, not fraud evidence: it
            # never flips a clean verdict, but it escalates an already-active
            # risk by one severity level (crowd-gathering scenarios in dense
            # urban zones warrant extra scrutiny on top of other signals).
            risk_score = "CRITICAL" if risk_score == "HIGH" else "HIGH"
        parts = []
        if sim_swapped:
            parts.append("SIM swap evidence was present.")
        if number_verification_risk:
            parts.append(
                f"Number Verification returned {number_verification_status} for the presented MSISDN, "
                "so the number could not be silently bound to the subscriber's device."
            )
        if not verification_match:
            parts.append("The location verification did not match the expected network context.")
        if roaming_status == "INTERNATIONAL_ROAMING":
            parts.append("The subscriber was observed on an international roaming context.")
        if unreachable_risk:
            parts.append("The device was unreachable, preventing secondary verification.")
        if roaming_policy_violation:
            parts.append("The transaction violated the enforced roaming policy by using international roaming.")
        if congestion_high_risk:
            parts.append(
                "The subscriber's serving cell reported sustained High congestion, "
                "consistent with crowd-gathering scenarios that warrant scrutiny."
            )
        elif congestion_medium:
            parts.append("The subscriber's serving cell reported Medium congestion as contextual corroboration.")
        if memory_hits:
            parts.append(
                "Prior incident memory for the subscriber corroborates elevated risk "
                f"({('high' if high_severity_history else 'moderate')}-severity history); "
                "recurrence evidence is weighted into this verdict."
            )
        if amount_risk:
            parts.append(f"The transaction amount of ${amount:,.2f} exceeded the standard auto-approval threshold.")
        if qod_active:
            parts.append("A QoD-assisted step-up session was provisioned for the transaction.")
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
        if not risk_signal and not amount_risk:
            recommended_action = (
                "Escalate with step-up verification and optionally a QoD-assisted session; "
                "the subscriber's prior incident history drives this escalation."
            )
        else:
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
            "number_verification_match": number_verified,
            "number_verification_status": number_verification_status,
            "max_congestion_level": max_congestion_level or None,
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
    narrative_overridden = False

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
            narrative_overridden = True
    # Risk score floor
    if det_risk and final_assessment.get("risk_score"):
        det_val = severity.get(det_risk, 2)
        model_val = severity.get(final_assessment.get("risk_score"), 2)
        if model_val < det_val:
            mismatch_reasons.append(
                f"CrewAI attempted to lower risk_score from '{det_risk}' to '{final_assessment.get('risk_score')}'. Preserving deterministic risk '{det_risk}'."
            )
            final_assessment["risk_score"] = det_risk
            narrative_overridden = True

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
    # When the downgrade floor forced a status and/or risk_score override, the
    # model's free-text verdict is untrusted: it must not contradict the
    # enforced structured verdict (e.g. prose claiming APPROVED while the floor
    # kept STEP_UP_REQUIRED). Substitute the deterministic narrative, which is
    # coherent with the grounded verdict by construction.
    if narrative_overridden:
        mismatch_reasons.append(
            "CrewAI narrative text contradicted the enforced structured verdict; "
            "substituting deterministic reasoning for coherence."
        )
        final_assessment["reasoning"] = deterministic_output["assessment"].get("reasoning")
        final_assessment["recommended_action"] = deterministic_output["assessment"].get("recommended_action")

    reasoning_raw = parsed_output.get("reasoning") or deterministic_output["assessment"].get("reasoning")
    if escalation_capped or narrative_overridden:
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
    elif not escalation_capped and not narrative_overridden:
        final_assessment["reasoning"] = reasoning_raw

    return final_assessment, mismatch_reasons


def _is_rate_limit_or_availability_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "429",
            "402",
            "413",
            "rate_limit",
            "rate limit",
            "quota",
            "tokens per minute",
            "tpm",
            "request too large",
            "insufficient credits",
            "no longer available",
            "temporarily unavailable",
            "service unavailable",
            "overloaded",
            "resource_exhausted",
        )
    )


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


# --- Provider reachability gate -------------------------------------------
# A configured key is not enough: egress from the deployed host (e.g. Render)
# can hang on a provider for the whole LLM budget, silently degrading every
# audit to deterministic. Probe reachability (cached) up front so a blocked
# host falls straight through to the fast deterministic path instead of
# burning ~75s on dead connections.
_PROBE_TTL_S = 120.0
_PROBE_TIMEOUT_S = 6.0
_PROBE_CACHE: Dict[str, float] = {}
_PROBE_CACHE_LOCK = threading.Lock()


def _probe_targets() -> Dict[str, tuple[str, Dict[str, str]]]:
    targets: Dict[str, tuple[str, Dict[str, str]]] = {}
    if settings.GROQ_API_KEY:
        targets["groq"] = ("https://api.groq.com/openai/v1/models", {"Authorization": f"Bearer {settings.GROQ_API_KEY}"})
    if settings.CEREBRAS_API_KEY:
        targets["cerebras"] = ("https://api.cerebras.ai/v1/models", {"Authorization": f"Bearer {settings.CEREBRAS_API_KEY}"})
    if settings.OPENROUTER_API_KEY:
        targets["openrouter"] = ("https://openrouter.ai/api/v1/models", {"Authorization": f"Bearer {settings.OPENROUTER_API_KEY}"})
    if settings.GOOGLE_API_KEY:
        targets["gemini"] = (f"https://generativelanguage.googleapis.com/v1beta/models?key={settings.GOOGLE_API_KEY}", {})
    return targets


def _probe_once(provider: str, url: str, headers: Dict[str, str]) -> bool:
    # Any HTTP response (even 401 for a bad key) proves the host is reachable
    # from this network; only a connect/TLS/DNS failure means "unreachable".
    try:
        import requests

        requests.get(url, headers=headers, timeout=_PROBE_TIMEOUT_S)
        return True
    except Exception:
        return False


def _reachable_providers() -> Dict[str, bool]:
    targets = _probe_targets()
    if not targets:
        return {}
    now = time.monotonic()
    with _PROBE_CACHE_LOCK:
        expired = [p for p, cached_at in _PROBE_CACHE.items() if now - cached_at >= _PROBE_TTL_S]
        for p in expired:
            _PROBE_CACHE.pop(p, None)
        cached = {p: True for p in _PROBE_CACHE}
    missing = {p: (u, h) for p, (u, h) in targets.items() if p not in cached}
    if missing:
        with ThreadPoolExecutor(max_workers=len(missing)) as pool:
            futures = {pool.submit(_probe_once, p, u, h): p for p, (u, h) in missing.items()}
            results = {futures[f]: f.result() for f in futures}
        with _PROBE_CACHE_LOCK:
            for p, reachable in results.items():
                if reachable:
                    _PROBE_CACHE[p] = time.monotonic()
        cached.update(results)
    return cached


def _mark_model_cooldown(model: str, exc: Optional[Exception] = None) -> None:
    _PROVIDER_COOLDOWN[model] = time.monotonic() + _cooldown_window_from_error(exc)


def _mark_failed_models(specialist_model: str, auditor_model: str, exc: Optional[Exception] = None) -> None:
    """Cooldown only the models implicated by the failure so a healthy partner
    in the pair is not poisoned and dragged out of the chain. Falls back to
    provider-level matching (e.g. OpenRouter credit errors), then to the whole
    pair when the error carries no identifying signal."""
    text = str(exc or "")
    models = [specialist_model, auditor_model]
    matched = [model for model in models if model.split("/")[-1] in text]
    if not matched:
        matched = [model for model in models if model.split("/", 1)[0] in text]
    if not matched:
        matched = models
    for model in matched:
        _mark_model_cooldown(model, exc)


def _cooldown_window_from_error(exc: Optional[Exception]) -> float:
    """Honor the provider's own retry hint; daily-quota errors (TPD / free-tier
    per-model request caps) reset on the provider's schedule, so park the model
    until the next nightly reset instead of retrying within seconds."""
    if exc is None:
        return _PROVIDER_COOLDOWN_WINDOW_S
    text = str(exc).lower()
    window = _PROVIDER_COOLDOWN_WINDOW_S
    match = re.search(r"try again in (\d+)m([\d.]+)?s?", text)
    if match:
        window = float(match.group(1)) * 60.0 + float(match.group(2) or 0.0)
    else:
        match = re.search(r"retrydelay[\"':=\s]+([\d.]+)", text)
        if match:
            window = float(match.group(1))
        else:
            match = re.search(r"(?:try again|retry) in ([\d.]+)s", text)
            if match:
                window = float(match.group(1))
    daily_cap_markers = (
        "tokens per day",
        "tpdamount",
        "requests per day",
        "rpdamount",
        "perdayperproject",
        "free tier",
        "free-tier",
        "generatecontentfree",
        "credits",
    )
    if any(marker in text for marker in daily_cap_markers):
        window = max(window, 6 * 3600.0)
    return min(window, 12 * 3600.0)


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
        evidence = [item for item in executed_tool_results if item.get("name") in {"check_sim_swap", "check_roaming_status", "verify_number"}]
    elif role == "network":
        evidence = [item for item in executed_tool_results if item.get("name") in {"verify_location", "check_device_reachability", "create_qod_session", "get_congestion_insights"}]
    else:
        evidence = [item for item in executed_tool_results if item.get("name") in {"check_sim_swap", "check_roaming_status", "verify_location", "check_device_reachability", "create_qod_session", "verify_number", "get_congestion_insights"}]

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


# Per-pair cap on a single crew LLM attempt. Keeps a hang/reachability problem on
# one provider from eating the whole multi-pair budget: fail over to the next
# provider after ~25s instead of blocking the request for minutes.
_LLM_ATTEMPT_BUDGET_S: float = 25.0


def run_specialist_crew(
    request_context: Dict[str, Any],
    memory_context: List[Dict[str, Any]],
    tool_results: List[Dict[str, Any]],
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    llm_time_budget_s: float = 75.0,
) -> Dict[str, Any]:
    """Run a real CrewAI specialist workflow and fall back to deterministic synthesis on errors.

    When `progress_callback` is provided, it is invoked (from any thread) with
    small JSON-serializable event dicts so a streaming transport can animate the
    pipeline: tool executions, deterministic synthesis, and LLM layer activity.

    `llm_time_budget_s` caps the wall-clock time spent on the LLM CrewAI chain
    (model-pair retries burn minutes when providers are rate-limited). When the
    budget expires the crew hard-falls back to the deterministic engine, so
    callers (audit stream, drill) always complete inside their own deadlines.
    """
    def _emit(event_type: str, **payload: Any) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback({"type": event_type, **payload})
        except Exception:
            logger.exception("Progress callback failed for event %s", event_type)

    msisdn = request_context.get("msisdn", "")
    amount = float(request_context.get("amount", 0.0))
    longitude = float(request_context.get("longitude", 46.7))
    latitude = float(request_context.get("latitude", 24.7))
    request_qod = bool(request_context.get("request_qod"))

    _t_start = time.monotonic()
    transaction_type = str(request_context.get("transaction_type", "")).upper()

    metadata = request_context.get("metadata") or {}
    try:
        geofence_radius_meters = int(float(metadata.get("geofence_radius_meters", 5000)))
    except (TypeError, ValueError):
        geofence_radius_meters = 5000
    if geofence_radius_meters <= 0:
        geofence_radius_meters = 5000
    enforce_roaming_policy = bool(metadata.get("enforce_roaming_policy"))

    # The telemetry tools are independent SDK calls. Running them concurrently
    # removes the serial latency without changing which tools run. QoD is
    # provisioned separately below, after the deterministic risk signal has
    # been computed, so that high-risk flows auto-provision a session even when
    # the amount is low and the caller did not explicitly request one.
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
        ("verify_number", lambda: _run_tool_payload("verify_number", verify_number, msisdn=msisdn)),
        ("get_congestion_insights", lambda: _run_tool_payload("get_congestion_insights", get_congestion_insights, msisdn=msisdn)),
    ]

    logger.info("verify_location called with radius=%s for msisdn=%s", geofence_radius_meters, msisdn)
    _emit("tools:start", count=len(jobs))
    with ThreadPoolExecutor(max_workers=len(jobs)) as _pool:
        _futures = [(_name, _pool.submit(_fn)) for _name, _fn in jobs]
        executed_tool_results = []
        for _name, _fut in _futures:
            started = time.monotonic()
            _payload = _fut.result()
            duration_ms = round((time.monotonic() - started) * 1000, 1)
            if isinstance(_payload, dict):
                _payload = dict(_payload)
                _payload["duration_ms"] = duration_ms
            executed_tool_results.append(_payload)
            _emit(
                "tool:done",
                tool=_name,
                source=_payload.get("source", "unknown") if isinstance(_payload, dict) else "unknown",
                status="ok" if isinstance(_payload, dict) and _payload.get("status_code", 200) < 400 else "error",
                duration_ms=duration_ms,
            )
    _t_tools = time.monotonic()

    # Pre-compute the deterministic risk signal from the base telemetry so the
    # QoD decision can react to actual risk (auto-provision on risk), not just
    # to the amount threshold or an explicit caller flag.
    risk_scan = synthesize_specialist_assessment(
        request_context,
        executed_tool_results,
        memory_context,
        enforce_roaming_policy=enforce_roaming_policy,
    )
    risk_signal = risk_scan["assessment"].get("status") != "APPROVED"
    _emit(
        "synthesis:done",
        status=risk_scan["assessment"].get("status"),
        risk_score=risk_scan["assessment"].get("risk_score"),
        signal_count=len(risk_scan.get("trace", [])),
    )

    # QoD is a consequence of risk or of a high-value flow, never of the
    # auto-provision flag alone: a clean low-amount transaction must not show a
    # "QoD REQUESTED" step-up next to an APPROVED verdict.
    if amount >= 25000 or risk_signal:
        _emit("qod:start", reason="amount_threshold" if amount >= 25000 else "risk_signal")
        executed_tool_results.append(_run_tool_payload("create_qod_session", create_qod_session, msisdn=msisdn))
        _emit("qod:done", status="ok")

    deterministic_output = synthesize_specialist_assessment(
        request_context,
        executed_tool_results,
        memory_context,
        enforce_roaming_policy=enforce_roaming_policy,
    )
    fallback_trace = deterministic_output.get("trace", [])
    failure_reason = "CrewAI not executed"
    _t_deterministic = time.monotonic()

    def _timing(llm_ms: float) -> Dict[str, Any]:
        return {
            "tools_ms": round((_t_tools - _t_start) * 1000, 1),
            "deterministic_ms": round((_t_deterministic - _t_tools) * 1000, 1),
            "llm_ms": round(llm_ms * 1000, 1),
        }

    force_deterministic = bool(request_context.get("force_deterministic"))

    provider_available = any(
        [
            settings.GROQ_API_KEY,
            settings.GOOGLE_API_KEY,
            settings.OPENROUTER_API_KEY,
        ]
    )
    reachable = _reachable_providers() if provider_available else {}
    providers_unreachable = provider_available and not any(reachable.values())
    if providers_unreachable:
        logger.warning(
            "Provider egress unreachable from this host (%s); using fast deterministic fallback",
            sorted(reachable),
        )
    if force_deterministic or not provider_available or providers_unreachable:
        reason = ("forced_deterministic" if force_deterministic
                  else "no_provider_credentials" if not provider_available
                  else "providers_unreachable")
        logger.warning("Using deterministic specialist fallback for crew workflow (%s)", reason)
        _emit("llm:fallback", reason=reason)
        _emit(
            "crew:done",
            status=deterministic_output["assessment"].get("status"),
            risk_score=deterministic_output["assessment"].get("risk_score"),
            used_fallback=True,
        )
        return {
            "assessment": deterministic_output["assessment"],
            "tool_results": executed_tool_results,
            "trace": fallback_trace,
            "raw_output": f"Deterministic fallback used because the LLM provider layer is unavailable from this host (reason: {reason}).",
            "used_fallback": True,
            "timing": _timing(0.0),
            "providers_reachable": reachable,
        }

    def _available_models() -> tuple[list[str], list[str]]:
        return (
            [model for model in MODEL_CHAIN["specialist"] if _model_provider_available(model) and not _model_in_cooldown(model)],
            [model for model in MODEL_CHAIN["auditor"] if _model_provider_available(model) and not _model_in_cooldown(model)],
        )

    def _build_available_pairs() -> List[tuple[str, str]]:
        supported_specialist_models, supported_auditor_models = _available_models()
        pairs: List[tuple[str, str]] = []
        for idx in range(max(len(supported_specialist_models), len(supported_auditor_models))):
            specialist_model = supported_specialist_models[idx] if idx < len(supported_specialist_models) else supported_specialist_models[-1]
            auditor_model = supported_auditor_models[idx] if idx < len(supported_auditor_models) else supported_auditor_models[-1]
            if (specialist_model, auditor_model) not in pairs:
                pairs.append((specialist_model, auditor_model))
        return pairs

    fallback_model_pairs = _build_available_pairs()
    if not fallback_model_pairs:
        # Every configured model is in cooldown (all rate-limited). Lift the
        # cooldown so a provider can serve this request instead of failing
        # straight to the deterministic fallback.
        _PROVIDER_COOLDOWN.clear()
        fallback_model_pairs = _build_available_pairs()
    if not fallback_model_pairs:
        logger.warning("Insufficient provider-backed models for specialist/auditor workflow; using deterministic specialist fallback")
        _emit("llm:fallback", reason="insufficient_models")
        _emit(
            "crew:done",
            status=deterministic_output["assessment"].get("status"),
            risk_score=deterministic_output["assessment"].get("risk_score"),
            used_fallback=True,
        )
        return {
            "assessment": deterministic_output["assessment"],
            "tool_results": executed_tool_results,
            "trace": fallback_trace,
            "raw_output": "Deterministic fallback used because the specialist/auditor model chain could not be built.",
            "used_fallback": True,
        }

    last_error: Exception | None = None
    deadline = time.monotonic() + llm_time_budget_s

    class _LLMBudgetExceededError(Exception):
        pass

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
            remaining_total = deadline - time.monotonic()
            if remaining_total <= 0:
                raise _LLMBudgetExceededError(f"LLM phase budget ({llm_time_budget_s}s) exhausted")
            # Per-attempt cap: a provider whose egress hangs for the whole budget
            # would otherwise starve every other provider (each pair retry only
            # starts after the previous one exhausted the total deadline). Cap each
            # attempt so a dead/hung provider fails over to the next pair quickly.
            remaining = min(remaining_total, _LLM_ATTEMPT_BUDGET_S)
            # Run the crew in a worker so the budget can be enforced. On the
            # success path this behaves exactly like the old `with` block. On a
            # real timeout, shutdown(wait=False) lets us respond immediately
            # instead of the `with`-block shutdown(wait=True) waiting until the
            # hung LLM call happens to finish — which was silently defeating the
            # whole budget mechanism.
            pool = ThreadPoolExecutor(max_workers=1)
            try:
                crew_future = pool.submit(crew.kickoff)
                crew_output = crew_future.result(timeout=remaining)
            except TimeoutError as exc:
                pool.shutdown(wait=False, cancel_futures=True)
                raise _LLMBudgetExceededError(f"LLM phase budget ({llm_time_budget_s}s) exhausted after {exc}") from exc
            except BaseException:
                pool.shutdown(wait=False, cancel_futures=True)
                raise
            else:
                pool.shutdown(wait=True)
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
        except _LLMBudgetExceededError as exc:
            last_error = exc
            logger.warning("CrewAI LLM phase exceeded its budget with %s/%s; falling back to deterministic synthesis", specialist_model, auditor_model)
            return None
        except Exception as exc:
            last_error = exc
            if _is_rate_limit_or_availability_error(exc) or _is_crew_tool_validation_error(exc):
                if _is_rate_limit_or_availability_error(exc):
                    _mark_failed_models(specialist_model, auditor_model, exc)
                logger.warning("CrewAI model chain hit retryable/fallback-worthy error with %s/%s: %s", specialist_model, auditor_model, exc)
            else:
                _mark_failed_models(specialist_model, auditor_model)
                logger.warning("CrewAI specialist workflow failed unexpectedly for %s/%s; trying next model pair: %s", specialist_model, auditor_model, exc)
            # Every failure moves to the next model in the chain. A dead/missing
            # model should be the least catastrophic outcome, not a request crash.
            return None

    cooldown_lifted = False
    model_index = 0
    while time.monotonic() < deadline:
        pairs = _build_available_pairs()
        if not pairs:
            if cooldown_lifted:
                logger.warning("CrewAI specialist workflow exhausted all fallback models; using deterministic fallback")
                break
            # Every configured model rate-limited mid-chain; lift cooldowns once
            # so a recovered provider can serve this request.
            _PROVIDER_COOLDOWN.clear()
            cooldown_lifted = True
            continue
        specialist_model, auditor_model = pairs[0]
        _emit("llm:start", specialist=specialist_model, auditor=auditor_model, tier=model_index + 1)
        crew_run = _try_crew_run(specialist_model, auditor_model, model_index)
        if crew_run is not None:
            crew_run["timing"] = _timing(time.monotonic() - _t_deterministic)
            _emit("llm:done", specialist=specialist_model, auditor=auditor_model, tier=model_index + 1)
            _emit(
                "crew:done",
                status=crew_run["assessment"].get("status"),
                risk_score=crew_run["assessment"].get("risk_score"),
                used_fallback=bool(crew_run.get("used_fallback")),
            )
            return crew_run
        model_index += 1

    if last_error is not None:
        failure_reason = str(last_error)
    logger.warning("CrewAI specialist workflow exhausted all fallback models; using deterministic fallback: %s", failure_reason)
    _emit("llm:fallback", reason=failure_reason)
    _emit(
        "crew:done",
        status=deterministic_output["assessment"].get("status"),
        risk_score=deterministic_output["assessment"].get("risk_score"),
        used_fallback=True,
    )

    return {
        "assessment": deterministic_output["assessment"],
        "tool_results": executed_tool_results,
        "trace": fallback_trace,
        "raw_output": f"Deterministic fallback used after CrewAI failure: {failure_reason}",
        "used_fallback": True,
        "timing": _timing(time.monotonic() - _t_deterministic),
        "providers_reachable": reachable,
    }
