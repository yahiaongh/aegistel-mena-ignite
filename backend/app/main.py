# backend/app/main.py
import os
import sys
import warnings

import asyncio
import collections
import io
import json
import logging
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from app.text_normalization import TTSTextNormalizer

from app.core.config import settings

warnings.filterwarnings("ignore", category=DeprecationWarning)

load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env")

from app.agents.graph_orchestrator import execute_audit
from app.agents.memory_agent import memory_engine
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
    items = []
    for item in incidents[:limit]:
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
            logger.warning("Deepgram TTS failed: %s", exc)

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

DRILL_TIMEOUT_SECONDS = 180


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
    except Exception as exc:
        logger.error("Adversarial drill failed: %s\n%s", exc, traceback.format_exc())
        return _audit_error_response(exc, 502)