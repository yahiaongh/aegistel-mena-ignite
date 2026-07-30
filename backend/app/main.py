import io
from typing import Any, Dict

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from app.agents.graph_orchestrator import execute_audit
from app.api.websocket_router import router as websocket_router
from app.schemas.telemetry import AuditRequest, AuditResponse

load_dotenv()

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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

router = APIRouter(prefix="/api", tags=["AegisTel Core"])


@router.get("/health")
async def health() -> Dict[str, Any]:
    return {"status": "ok", "service": "AegisTel", "mode": "autonomous"}


@router.post("/v1/audit", response_model=AuditResponse)
async def audit_transaction(request: AuditRequest) -> AuditResponse:
    """Executes the autonomous LangGraph workflow using Nokia NaC CAMARA APIs."""
    return await execute_audit(request)


@router.post("/audio/tts")
async def text_to_speech(
    text: str = Form(...),
    voice: str = Form("ar-EG-ShakirNeural"),
    rate: str = Form("-5%"),
    pitch: str = Form("-2Hz"),
):
    if edge_tts is None:
        return JSONResponse(status_code=503, content={"detail": "TTS backend is unavailable in this environment"})
    try:
        communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate, pitch=pitch)
        audio_stream = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_stream.write(chunk["data"])
        audio_stream.seek(0)
        return StreamingResponse(
            audio_stream,
            media_type="audio/mpeg",
            headers={"Content-Disposition": "attachment; filename=speech.mp3"},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"TTS Error: {exc}") from exc


app.include_router(router)
app.include_router(websocket_router)