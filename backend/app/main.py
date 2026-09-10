# backend/app/main.py
import os
import sys
import warnings

import asyncio
import collections
import io
import json
import logging
import secrets
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from app.text_normalization import TTSTextNormalizer

from app.core.config import settings

warnings.filterwarnings("ignore", category=DeprecationWarning)

load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env")

from app.agents.graph_orchestrator import execute_audit
from app.agents.memory_agent import memory_engine
from app.copilot_agent import answer as copilot_answer
from app.feedback_store import build_summary, list_feedback, sanitize_ratings, submit_feedback
from app.schemas.telemetry import AuditRequest, AuditResponse, QoDProvisionRequest

app = FastAPI(
    title="AegisTel MENA Ignite API",
    description="Autonomous Telecom Multi-Agent Fraud Engine using Nokia Network as Code CAMARA APIs",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

router = APIRouter(prefix="/api", tags=["AegisTel Core"])
logger = logging.getLogger(__name__)
_normalizer = TTSTextNormalizer()

# In-process sliding-window rate limiter (per apparent client IP, per bucket).
# This is a lightweight demo-grade guard, not a distributed WAF:
# - Runs in-process; does not coordinate across instances.
# - Keys on `request.client.host` → collapses all Render users behind the
#   same proxy IP into one bucket.
# Production deployments must rate-limit at the edge (Cloudflare/Render proxy
# + authenticated per-tenant quota plans with proper X-Forwarded-For handling).
_RATE_WINDOWS: Dict[str, "collections.deque[float]"] = {}
_RATE_LOCK = threading.Lock()


def _rate_limit(bucket: str, limit: int, window_seconds: int = 60):
    def dependency(request: Request) -> None:
        client = request.client.host if request.client else "unknown"
        key = f"{bucket}:{client}"
        now = time.monotonic()
        with _RATE_LOCK:
            hits = _RATE_WINDOWS.setdefault(key, collections.deque())
            while hits and hits[0] <= now - window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                retry = max(0, int(window_seconds - (now - hits[0]))) if hits else 0
                raise HTTPException(
                    status_code=429,
                    detail="rate_limit_exceeded",
                    headers={"Retry-After": str(retry)},
                )
            hits.append(now)

    return dependency


_audit_limiter = _rate_limit("audit", max(1, settings.AEGISTEL_API_RATE_LIMIT_PER_MIN), 60)
_feedback_limiter = _rate_limit("feedback", 30, 60)
_copilot_limiter = _rate_limit("copilot", 60, 60)
_tts_limiter = _rate_limit("tts", 30, 60)
_drill_limiter = _rate_limit("drill", 30, 60)
_qod_limiter = _rate_limit("qod", 10, 60)


def _resolve_admin_token(request: Request) -> str:
    """Accept the admin token as Bearer, X-Admin-Token header, or ?token=."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-admin-token", "") or request.query_params.get("token", "")


def _require_operator(request: Request) -> str:
    """FastAPI dependency for operator-only endpoints (history, memory wipe).

    Requires AEGISTEL_ADMIN_KEY to be configured AND the caller to present a
    matching token (Bearer, X-Admin-Token, or ?token=). Fails closed: 503 when
    the key is unset, 401 on missing/mismatched credentials.
    """
    supplied = _resolve_admin_token(request)
    if not settings.AEGISTEL_ADMIN_KEY:
        raise HTTPException(
            status_code=503,
            detail="Operator API unavailable: AEGISTEL_ADMIN_KEY is not configured on this deployment.",
        )
    if not supplied or not secrets.compare_digest(supplied, settings.AEGISTEL_ADMIN_KEY):
        raise HTTPException(status_code=401, detail="Operator authentication required.")
    return supplied


_TENANT_NAME_PATTERN = "abcdefghijklmnopqrstuvwxyz0123456789-_"
_TENANT_KEY_MAP_CACHE: Optional[Dict[str, str]] = None


def _tenant_key_map() -> Dict[str, str]:
    """Parse AEGISTEL_TENANT_API_KEYS ("tenant_a=key1,tenant_b=key2") once.

    Returns {tenant_id: api_key}. Malformed pairs are skipped loudly so a
    typo in the env file never silently widens or narrows access.
    """
    global _TENANT_KEY_MAP_CACHE
    if _TENANT_KEY_MAP_CACHE is not None:
        return _TENANT_KEY_MAP_CACHE
    mapping: Dict[str, str] = {}
    raw = (settings.AEGISTEL_TENANT_API_KEYS or "").strip()
    if raw:
        for pair in raw.split(","):
            pair = pair.strip()
            if not pair:
                continue
            if "=" not in pair:
                logger.warning("Ignoring malformed AEGISTEL_TENANT_API_KEYS pair %r (expected tenant=key)", pair)
                continue
            tenant, _, key = pair.partition("=")
            tenant = tenant.strip().lower()
            key = key.strip()
            if not all(ch in _TENANT_NAME_PATTERN for ch in tenant) or not key:
                logger.warning("Ignoring malformed AEGISTEL_TENANT_API_KEYS pair %r", pair)
                continue
            mapping[tenant] = key
    _TENANT_KEY_MAP_CACHE = mapping
    return mapping


def _resolve_tenant(request: Request) -> str:
    """Server-side tenant resolution. The tenant namespace is ALWAYS derived
    from the client's credential:

      * A valid tenant API key (from AEGISTEL_TENANT_API_KEYS) maps to that
        tenant's namespace — memory writes/reads are scoped to it.
      * A key that does not match any tenant is rejected with 401 (fails closed,
        so a guessed key can never land in the default namespace).
      * An anonymous caller (no credential) lands in AEGISTEL_DEFAULT_TENANT
        only — never in a real tenant namespace — unless
        AEGISTEL_ALLOW_ANON_AUDIT=false, in which case anonymous traffic is 401.

    The audit JSON body is irrelevant here: it cannot influence the tenant.
    """
    supplied = _resolve_admin_token(request)
    if supplied:
        for tenant, key in _tenant_key_map().items():
            if secrets.compare_digest(supplied, key):
                return tenant
        raise HTTPException(status_code=401, detail="Invalid API key.")
    if not settings.AEGISTEL_ALLOW_ANON_AUDIT:
        raise HTTPException(status_code=401, detail="API key required for audit access.")
    return settings.AEGISTEL_DEFAULT_TENANT


def _require_provisioning_principal(request: Request) -> str:
    """Dependency for chargeable/consented operations (QoD provisioning).

    Never accepts anonymous callers: creating a QoD session borrows a chargeable
    shared network resource, so the actor must present a real credential. Accepts
    the operator admin key (principal 'operator') or a valid tenant API key
    (principal = server-derived tenant namespace).
    """
    supplied = _resolve_admin_token(request)
    if not supplied:
        raise HTTPException(status_code=401, detail="Authentication required for this action.")
    if settings.AEGISTEL_ADMIN_KEY and secrets.compare_digest(supplied, settings.AEGISTEL_ADMIN_KEY):
        # The operator key is a demo/platform control, not a cross-tenant
        # impersonation credential. It can confirm only the default demo
        # tenant's decisions; real tenant decisions require that tenant's key.
        return settings.AEGISTEL_DEFAULT_TENANT
    for tenant, key in _tenant_key_map().items():
        if secrets.compare_digest(supplied, key):
            return tenant
    raise HTTPException(status_code=401, detail="Invalid API key.")


def _count_active_tools() -> int:
    try:
        import importlib

        tools_mod = importlib.import_module("app.agents.tools")
        tool_names = [
            "check_device_reachability",
            "check_device_swap",
            "check_number_recycling",
            "check_roaming_status",
            "check_sim_swap",
            "create_qod_session",
            "get_congestion_insights",
            "verify_location",
            "verify_number",
        ]
        return sum(1 for name in tool_names if hasattr(tools_mod, name))
    except Exception:
        return 0


@router.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "AegisTel",
        "mode": "autonomous",
        "active_tool_count": _count_active_tools(),
        "providers_configured": {
            "groq": bool(settings.GROQ_API_KEY),
            "gemini": bool(settings.GOOGLE_API_KEY),
            "openrouter": bool(settings.OPENROUTER_API_KEY),
            "cerebras": bool(settings.CEREBRAS_API_KEY),
            "deepgram": bool(settings.DEEPGRAM_API_KEY),
        },
    }


def _audit_error_response(exc: Exception, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": "audit_pipeline_failure",
            "detail": str(exc),
            "type": type(exc).__name__,
        },
    )


def _is_rate_limit_or_availability_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in ("429", "rate_limit", "quota", "no longer available", "temporarily unavailable", "service unavailable", "overloaded"))


AUDIT_TIMEOUT_SECONDS = 120
AUDIT_STREAM_TIMEOUT_SECONDS = 120
QOD_DECISION_MAX_AGE_SECONDS = 15 * 60

@router.post("/v1/audit", response_model=AuditResponse)
async def audit_transaction(
    request: AuditRequest,
    tenant: str = Depends(_resolve_tenant),
    _rate_limited: None = Depends(_audit_limiter),
) -> AuditResponse:
    """Executes the autonomous LangGraph workflow using Nokia NaC CAMARA APIs."""
    try:
        return await asyncio.wait_for(execute_audit(request, tenant_id=tenant), timeout=AUDIT_TIMEOUT_SECONDS)
    except HTTPException as exc:
        logger.error("Audit request failed with HTTPException: %s", exc)
        return _audit_error_response(exc, exc.status_code)
    except (TimeoutError, asyncio.TimeoutError) as exc:
        logger.error("Audit request timed out after %ss: %s", AUDIT_TIMEOUT_SECONDS, exc)
        return _audit_error_response(exc, 504)
    except (ConnectionError, OSError) as exc:
        logger.error("Audit request connection failed: %s\n%s", exc, traceback.format_exc())
        return _audit_error_response(exc, 502)
    except RuntimeError as exc:
        if _is_rate_limit_or_availability_error(exc):
            logger.warning("Audit request hit a retryable model-side error: %s", exc)
            return _audit_error_response(exc, 429)
        logger.error("Unhandled audit runtime failure: %s\n%s", exc, traceback.format_exc())
        return _audit_error_response(exc, 502)
    except Exception as exc:
        logger.error("Unhandled audit failure: %s\n%s", exc, traceback.format_exc())
        return _audit_error_response(exc, 502)


@router.get("/diagnostics/provider_probe")
async def provider_probe(operator: str = Depends(_require_operator)) -> Dict[str, Any]:
    """Read-only connectivity probe to each configured LLM provider. Proves from
    inside the host (e.g. Render) which providers are reachable and that the
    configured key authenticates, so model-chain trouble can be told apart from
    egress/network trouble with one call.

    Operator-only: this endpoint fires authenticated requests at every paid or
    free-tier provider and reflects their status, so it is gated behind the same
    admin dependency as history/memory-wipe and fails closed (503) when
    AEGISTEL_ADMIN_KEY is not configured.
    """
    import requests as _requests

    def _probe(name: str, url: str, headers: dict, timeout: float = 12.0) -> Dict[str, Any]:
        started = time.monotonic()
        try:
            r = _requests.get(url, headers=headers, timeout=timeout)
            return {
                "provider": name,
                "reachable": True,
                "http": r.status_code,
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
                "detail": (r.text or "")[:120],
            }
        except Exception as exc:
            return {
                "provider": name,
                "reachable": False,
                "http": None,
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
                "detail": type(exc).__name__,
            }

    probes: List[Dict[str, Any]] = []
    if settings.GROQ_API_KEY:
        probes.append(_probe(
            "groq",
            "https://api.groq.com/openai/v1/models",
            {"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
        ))
    if settings.OPENROUTER_API_KEY:
        probes.append(_probe(
            "openrouter",
            "https://openrouter.ai/api/v1/models",
            {"Authorization": f"Bearer {settings.OPENROUTER_API_KEY}"},
        ))
    if settings.GOOGLE_API_KEY:
        probes.append(_probe(
            "gemini",
            f"https://generativelanguage.googleapis.com/v1beta/models?key={settings.GOOGLE_API_KEY}",
            {},
        ))
    if settings.CEREBRAS_API_KEY:
        probes.append(_probe(
            "cerebras",
            "https://api.cerebras.ai/v1/models",
            {"Authorization": f"Bearer {settings.CEREBRAS_API_KEY}"},
        ))
    if settings.DEEPGRAM_API_KEY:
        probes.append(_probe(
            "deepgram",
            "https://api.deepgram.com/v1/projects",
            {"Authorization": f"Token {settings.DEEPGRAM_API_KEY}"},
        ))
    return {"probes": probes}


@router.get("/diagnostics/number_verification")
async def number_verification_diagnostics(operator: str = Depends(_require_operator)) -> Dict[str, Any]:
    """Diagnostics for Number Verification endpoint.
    
    Proves from inside the host (e.g. Render) whether the Number Verification
    endpoint is reachable via SDK and/or REST, and whether the API key has
    the required entitlement. Gated behind operator auth.
    """
    from app.agents.tools import nac_client, NOKIA_API_KEY, NOKIA_BASE_URL
    import requests
    import time
    
    results = {
        "nokia_api_key_configured": NOKIA_API_KEY != "sandbox-key",
        "sdk_initialized": nac_client is not None,
        "sdk_has_number_verification": hasattr(nac_client, "number_verification") if nac_client else False,
        "sdk_modules": [a for a in dir(nac_client) if not a.startswith('_')] if nac_client else [],
        "sdk_test": None,
        "rest_test": None,
    }
    
    # Test SDK path
    if nac_client and hasattr(nac_client, "number_verification"):
        try:
            started = time.monotonic()
            verify_result = nac_client.number_verification.verify_v2(
                request={"phone_number": "+99999991000"}  # Known test number
            )
            verified = getattr(verify_result, "device_phone_number_verified", None)
            results["sdk_test"] = {
                "success": True,
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
                "verified": verified,
                "source": "Nokia NaC SDK",
            }
        except Exception as e:
            results["sdk_test"] = {
                "success": False,
                "error": f"{type(e).__name__}: {e}",
            }
    else:
        results["sdk_test"] = {
            "success": False,
            "error": "nac_client is None or missing number_verification attribute",
        }
    
    # Test REST path
    url = f"{NOKIA_BASE_URL}/number-verification/number-verification/v2/verify"
    try:
        started = time.monotonic()
        response = requests.post(
            url, json={"phoneNumber": "+99999991000"}, 
            headers={
                "Content-Type": "application/json",
                "x-rapid-api-host": "network-as-code.nokia.rapidapi.com",
                "x-rapidapi-key": NOKIA_API_KEY,
            },
            timeout=10
        )
        latency_ms = round((time.monotonic() - started) * 1000, 1)
        if response.status_code == 200:
            data = response.json()
            verified = data.get("devicePhoneNumberVerified")
            results["rest_test"] = {
                "success": True,
                "latency_ms": latency_ms,
                "http_status": 200,
                "verified": verified,
                "source": "Nokia NaC REST API",
            }
        else:
            results["rest_test"] = {
                "success": False,
                "latency_ms": latency_ms,
                "http_status": response.status_code,
                "error": response.text[:200],
            }
    except Exception as e:
        results["rest_test"] = {
            "success": False,
            "error": f"{type(e).__name__}: {e}",
        }
    
    return results


@router.get("/v1/history/{msisdn}")
async def audit_history(
    msisdn: str,
    limit: int = 10,
    operator: str = Depends(_require_operator),
):
    # Operator (admin) can view history for the default tenant namespace.
    # Tenant isolation applies to tenant API keys; the operator views the
    # default tenant's history (where demo/audit records are stored).
    tenant = settings.AEGISTEL_DEFAULT_TENANT
    incidents = memory_engine.list_all_incidents(msisdn, tenant_id=tenant)
    # local store is append-ordered (oldest first): serve the most RECENT
    # `limit` records so the operator's risk trend reflects current history,
    # not the transaction's first days.
    recent = incidents[-limit:] if len(incidents) > limit else incidents
    items = []
    for item in recent:
        metadata = item.get("metadata") or {}
        if isinstance(metadata, dict):
            items.append(
                {
                    "timestamp": item.get("created_at") or item.get("timestamp") or item.get("updated_at"),
                    "status": metadata.get("status"),
                    "risk_score": metadata.get("risk_score"),
                    "amount": metadata.get("amount"),
                    "roaming_status": metadata.get("roaming_status"),
                }
            )
    return {"msisdn": msisdn, "tenant": tenant, "count": len(items), "incidents": items}


@router.post("/memory/clear")
async def clear_tenant_memory(tenant: str = Depends(_resolve_tenant)):
    """Clear memory for the caller's tenant only. Scoped to the server-derived tenant."""
    result = memory_engine.clear_tenant_memory(tenant)
    return {
        "status": "success",
        "message": f"Memory cleared for tenant '{tenant}'",
        "details": result,
    }


@router.post("/memory/clear-all")
async def clear_all_memory(operator: str = Depends(_require_operator)):
    """Clears ALL tenants' memory. Superadmin-only: destructive administrative action."""
    if memory_engine.memory:
        try:
            memory_engine.clear_all_memory()
            return {"status": "success", "message": "All memory cleared (all tenants)"}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Memory clear error: {exc}") from exc
    memory_engine._local_store = []
    return {"status": "success", "message": "All local memory cleared (all tenants)"}


@router.post("/feedback", status_code=201)
async def submit_feedback_endpoint(
    request: Request, _rate_limited: None = Depends(_feedback_limiter)
) -> Dict[str, Any]:
    """Public feedback collection for judges, operators and demo visitors.

    Accepts independent 1-5 star ratings per feature (at least one is required),
    a quick emoji mood, an optional role, and optional free-text. Context about
    the current demo run (last MSISDN + verdict) is attached by the client.
    """
    try:
        payload: Dict[str, Any] = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON body: {exc}") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Feedback payload must be a JSON object.")

    ratings = sanitize_ratings(payload.get("ratings"))
    if not ratings:
        raise HTTPException(status_code=422, detail="At least one feature rating (1-5 stars) is required.")

    client_host = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent", "")[:300]
    meta = {"client_ip": client_host, "user_agent": user_agent}

    record = submit_feedback(payload, meta=meta)
    return {
        "ok": True,
        "id": record["id"],
        "thank_you": "Logged. Every stroke helps the build.",
        "ratings": record["ratings"],
    }


@router.get("/feedback")
async def read_feedback(request: Request, limit: int = 200) -> Dict[str, Any]:
    """Founder-only readback: per-feature averages, mood/role tallies, latest notes."""
    if not settings.AEGISTEL_ADMIN_KEY:
        return JSONResponse(status_code=503, content={"detail": "Feedback admin key is not configured on this server."})
    supplied = _resolve_admin_token(request)
    if not supplied or not secrets.compare_digest(supplied, settings.AEGISTEL_ADMIN_KEY):
        return JSONResponse(status_code=401, content={"detail": "Invalid or missing feedback admin token."})
    try:
        limit_value = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        limit_value = 200
    records = list_feedback(limit_value)
    return {
        "summary": build_summary(list_feedback(5000)),
        "latest": records,
    }


@router.post("/copilot/chat")
async def copilot_chat(
    payload: Dict[str, Any], _rate_limited: None = Depends(_copilot_limiter)
) -> Dict[str, Any]:
    """AegisTel copilot: grounded platform Q&A (audit, tools, verdicts, drill,
    business model, stack). Fast deterministic retrieval by default; pass
    `enhance: true` to let the swarm's model chain polish the grounded answer,
    degrading to the same grounded text if every provider is unavailable."""
    question = str(payload.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=422, detail="question is required.")
    raw_history = payload.get("history")
    history = [h for h in raw_history if isinstance(h, dict)] if isinstance(raw_history, list) else []
    return copilot_answer(
        question,
        history=history,
        last_topic=str(payload["last_topic"]) if payload.get("last_topic") else None,
        enhance=bool(payload.get("enhance", False)),
    )


@router.post("/audio/tts")
async def text_to_speech(
    text: str = Form(...),
    voice: str = Form("ar-EG-ShakirNeural"),
    rate: str = Form("-5%"),
    pitch: str = Form("-2Hz"),
    _rate_limited: None = Depends(_tts_limiter),
):
    def _audio_response(audio_bytes: bytes, source: str) -> StreamingResponse:
        audio_stream = io.BytesIO(audio_bytes)
        return StreamingResponse(
            audio_stream,
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": "attachment; filename=speech.mp3",
                "X-TTS-Source": source,
            },
        )

    if not text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")

    if not settings.DEEPGRAM_API_KEY:
        # Voice narration is Deepgram-only. Without a key the endpoint fails
        # closed with a clear hint; the dashboard degrades to the browser's
        # local speechSynthesis rather than inventing a voice pipe.
        return JSONResponse(
            status_code=503,
            content={
                "detail": "TTS requires a configured DEEPGRAM_API_KEY.",
                "hint": "Set DEEPGRAM_API_KEY on the deployment, or disable narration client-side.",
            },
        )

    normalized_text = _normalizer.normalize(text)

    # Deepgram is the only TTS path. If it fails with a configured key it is
    # surfaced loudly (no silent voice swap).
    try:
        import requests

        response = await asyncio.to_thread(
            requests.post,
            "https://api.deepgram.com/v1/speak?model=aura-asteria-en&encoding=mp3",
            headers={"Authorization": f"Token {settings.DEEPGRAM_API_KEY}"},
            json={"text": normalized_text},
            timeout=20,
        )
        response.raise_for_status()
        return _audio_response(response.content, "deepgram")
    except Exception as exc:
        logger.error("Deepgram TTS failed with a configured key; not degrading silently: %s", exc)
        return JSONResponse(
            status_code=503,
            content={
                "detail": f"Deepgram TTS failed: {exc}",
                "hint": "Check the DEEPGRAM_API_KEY on the deployment; the app will not silently switch to another voice provider.",
            },
        )


@router.post("/v1/audit/stream")
async def audit_transaction_stream(
    request: AuditRequest,
    tenant: str = Depends(_resolve_tenant),
    _rate_limited: None = Depends(_audit_limiter),
):
    """SSE variant of /v1/audit: emits pipeline progress events (tool calls,
    synthesis, LLM layer), then a final `result` event with the full audit
    response. Lets the dashboard animate the request/response flow in real time."""
    events: "collections.deque[Dict[str, Any]]" = collections.deque()
    events_lock = threading.Lock()

    def _emit(evt: Dict[str, Any]) -> None:
        with events_lock:
            events.append(evt)

    async def _runner() -> AuditResponse:
        return await asyncio.wait_for(
            execute_audit(request, progress_callback=_emit, tenant_id=tenant),
            timeout=AUDIT_STREAM_TIMEOUT_SECONDS,
        )

    async def _generator():
        task = asyncio.create_task(_runner())
        last_ping = time.monotonic()
        while True:
            with events_lock:
                batch = list(events)
                events.clear()
            for evt in batch:
                yield f"event: progress\ndata: {json.dumps(evt, default=str)}\n\n"
            if task.done():
                try:
                    result = await task
                    yield f"event: result\ndata: {result.model_dump_json()}\n\n"
                except asyncio.TimeoutError:
                    yield f"event: error\ndata: {json.dumps({'error': f'audit exceeded {AUDIT_STREAM_TIMEOUT_SECONDS}s', 'type': 'TimeoutError'})}\n\n"
                except HTTPException as exc:
                    yield f"event: error\ndata: {json.dumps({'error': str(exc.detail), 'type': 'HTTPException'})}\n\n"
                except Exception as exc:
                    logger.error("Streaming audit failed: %s\n%s", exc, traceback.format_exc())
                    yield f"event: error\ndata: {json.dumps({'error': str(exc), 'type': type(exc).__name__})}\n\n"
                break
            if time.monotonic() - last_ping > 15:
                yield ": keep-alive\n\n"
                last_ping = time.monotonic()
            await asyncio.sleep(0.1)

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/v1/audit/qod/provision")
async def provision_qod_session(
    request: QoDProvisionRequest,
    principal: str = Depends(_require_provisioning_principal),
    _rate_limited: None = Depends(_qod_limiter),
) -> Dict[str, Any]:
    """Create a QoD session for a subscriber — an EXPLICIT, confirmed action.

    This is deliberately NOT a side effect of the audit decision. Audits only
    return `qod_recommended` (and may surface that in the UI); they never
    provision. Provisioning a QoD session borrows a chargeable shared network
    resource, so this endpoint additionally requires:
      1. an authenticated client (operator admin key or a tenant API key), and
      2. the server-side bank-policy flag AEGISTEL_QOD_POLICY_ENABLED=true.
    The provisioning is recorded as a QOD_PROVISIONED incident in the actor's
    server-derived tenant namespace.
    """
    if not settings.AEGISTEL_QOD_POLICY_ENABLED:
        raise HTTPException(
            status_code=403,
            detail=(
                "QoD provisioning is disabled by bank policy (AEGISTEL_QOD_POLICY_ENABLED=false). "
                "The audit decision returns a recommendation only."
            ),
        )
    decision = memory_engine.find_audit_decision(
        str(request.audit_id), request.msisdn, principal
    )
    if decision is None:
        raise HTTPException(
            status_code=403,
            detail="QoD provisioning requires a tenant-scoped audit that recommended QoD.",
        )
    decision_metadata = decision.get("metadata") or {}
    created_at = decision.get("created_at")
    try:
        decision_time = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        age_seconds = (datetime.now(timezone.utc) - decision_time).total_seconds()
    except (TypeError, ValueError):
        age_seconds = QOD_DECISION_MAX_AGE_SECONDS + 1
    if not decision_metadata.get("qod_recommended") or age_seconds < 0 or age_seconds > QOD_DECISION_MAX_AGE_SECONDS:
        raise HTTPException(
            status_code=403,
            detail="QoD provisioning requires a fresh (15 minute) audit recommendation.",
        )
    if memory_engine.has_qod_provisioning(str(request.audit_id), request.msisdn, principal):
        raise HTTPException(
            status_code=409,
            detail="QoD has already been provisioned for this audit decision.",
        )
    try:
        from app.agents.tools import create_qod_session as _create_qod_session

        _call = _create_qod_session.run if hasattr(_create_qod_session, "run") else _create_qod_session
        raw = await asyncio.to_thread(
            _call,
            msisdn=request.msisdn,
            service_ip=settings.AEGISTEL_QOD_SERVICE_IP,
            profile=request.profile,
            duration_seconds=request.duration_seconds,
        )
        session: Dict[str, Any] = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    except Exception as exc:
        logger.error("QoD provisioning failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=502, detail=f"QoD provisioning failed: {exc}") from exc

    # Consent/approval trail, scoped to the principal's tenant namespace.
    try:
        memory_engine.record_incident(
            request.msisdn,
            f"QoD session provisioned (confirmed action) profile={request.profile}",
            metadata={
                "status": "QOD_PROVISIONED",
                "session_id": session.get("sessionId"),
                "qos_status": session.get("qosStatus"),
                "source": session.get("source"),
                "audit_id": str(request.audit_id),
                "profile": request.profile,
                "tenant_id": principal,
            },
        )
    except Exception as exc:
        logger.warning("Failed to record QoD provisioning trail: %s", exc)

    return {
        "provisioned": True,
        "msisdn": request.msisdn,
        "tenant": principal,
        "audit_id": str(request.audit_id),
        "session": session,
    }


app.include_router(router)

DRILL_TIMEOUT_SECONDS = 60


@router.post("/v1/drill/run")
async def adversarial_drill_run(
    request: Optional[Dict[str, Any]] = None, _rate_limited: None = Depends(_drill_limiter)
) -> Dict[str, Any]:
    """Red-team drill: executes adversarial plays against the blue-team crew and grades the defense."""
    try:
        from app.agents.drill_agent import run_adversarial_drill

        body = request or {}
        return await asyncio.wait_for(
            asyncio.to_thread(
                run_adversarial_drill,
                plays=body.get("plays"),
                use_llm=body.get("use_llm", True),
            ),
            timeout=DRILL_TIMEOUT_SECONDS,
        )
    except HTTPException as exc:
        return _audit_error_response(exc, exc.status_code)
    except (TimeoutError, asyncio.TimeoutError) as exc:
        # NOTE: on Python 3.11+ asyncio.TimeoutError subclasses OSError, so
        # this must be caught BEFORE the (ConnectionError, OSError) handler —
        # otherwise a drill that outlives its cap is mis-reported as a 502
        # instead of an honest timeout.
        logger.error("Adversarial drill timed out after %ss: %s", DRILL_TIMEOUT_SECONDS, exc)
        return _audit_error_response(exc, 504)
    except Exception as exc:
        logger.error("Adversarial drill failed: %s\n%s", exc, traceback.format_exc())
        return _audit_error_response(exc, 502)
