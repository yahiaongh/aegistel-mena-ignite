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
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, Form, HTTPException, Request
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
from app.schemas.telemetry import AuditRequest, AuditResponse

try:
    import edge_tts
except Exception:
    edge_tts = None

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


def _count_active_tools() -> int:
    try:
        import importlib

        tools_mod = importlib.import_module("app.agents.tools")
        tool_names = [
            "check_device_reachability",
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


@router.get("/diagnostics/provider_probe")
async def provider_probe() -> Dict[str, Any]:
    """Read-only connectivity probe to each configured LLM provider. Proves from
    inside the host (e.g. Render) which providers are reachable and that the
    configured key authenticates, so model-chain trouble can be told apart from
    egress/network trouble with one call."""
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

@router.post("/v1/audit", response_model=AuditResponse)
async def audit_transaction(request: AuditRequest) -> AuditResponse:
    """Executes the autonomous LangGraph workflow using Nokia NaC CAMARA APIs."""
    try:
        return await asyncio.wait_for(execute_audit(request), timeout=AUDIT_TIMEOUT_SECONDS)
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




@router.get("/v1/history/{msisdn}")
async def audit_history(msisdn: str, limit: int = 10):
    incidents = memory_engine.list_all_incidents(msisdn)
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
    return {"msisdn": msisdn, "count": len(items), "incidents": items}


@router.post("/memory/clear-all")
async def clear_all_memory():
    """Clears all memory."""
    if memory_engine.memory:
        try:
            memory_engine.clear_all_memory()
            return {"status": "success", "message": "All memory cleared"}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Memory clear error: {exc}") from exc
    memory_engine._local_store = []
    return {"status": "success", "message": "All local memory cleared"}


def _resolve_admin_token(request: Request) -> str:
    """Accept the admin token as Bearer, X-Admin-Token header, or ?token=."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-admin-token", "") or request.query_params.get("token", "")


@router.post("/feedback", status_code=201)
async def submit_feedback_endpoint(request: Request) -> Dict[str, Any]:
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
async def copilot_chat(payload: Dict[str, Any]) -> Dict[str, Any]:
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

    normalized_text = _normalizer.normalize(text)

    if settings.DEEPGRAM_API_KEY:
        # Deepgram is the REQUIRED path when a key is configured: surface a
        # failure loudly instead of silently swapping to edge_tts, or the demo
        # hears a different voice than intended and no one knows why.
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

    if edge_tts is None:
        return JSONResponse(status_code=503, content={"detail": "TTS backend is unavailable in this environment"})

    try:
        communicate = edge_tts.Communicate(text=normalized_text, voice=voice, rate=rate, pitch=pitch)
        audio_stream = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_stream.write(chunk["data"])
        audio_stream.seek(0)
        return StreamingResponse(
            audio_stream,
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": "attachment; filename=speech.mp3",
                "X-TTS-Source": "edge_tts",
            },
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"TTS Error: {exc}") from exc


@router.post("/v1/audit/stream")
async def audit_transaction_stream(request: AuditRequest):
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
            execute_audit(request, progress_callback=_emit),
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


app.include_router(router)

DRILL_TIMEOUT_SECONDS = 60


@router.post("/v1/drill/run")
async def adversarial_drill_run(request: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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