# app/main.py
import os
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from app.schemas.telemetry import AuditRequest, AuditResponse
from app.agents.graph_orchestrator import execute_audit

load_dotenv()

app = FastAPI(
    title="AegisTel MENA Ignite API",
    description="Autonomous Telecom Multi-Agent Fraud Engine using Nokia Network as Code",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class SynthesizeRequest(BaseModel):
    text: str

@app.post("/api/v1/audit", response_model=AuditResponse)
async def audit_transaction(request: AuditRequest):
    return await execute_audit(request)

@app.post("/api/v1/synthesize-alert")
async def synthesize_alert(req: SynthesizeRequest):
    elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY")
    if not elevenlabs_api_key:
        raise HTTPException(
            status_code=500,
            detail="ELEVENLABS_API_KEY environment variable is missing."
        )

    # Default to '21m00Tcm4TlvDq8ikWAM' (Rachel - universal premade voice)
    voice_id = os.getenv("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"

    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": elevenlabs_api_key,
    }

    payload = {
        "text": req.text,
        "model_id": "eleven_multilingual_v2",
    }

    client = httpx.AsyncClient(timeout=30.0)

    # 1. Inspect response status BEFORE starting the StreamingResponse stream
    req_obj = client.build_request("POST", url, json=payload, headers=headers)
    res = await client.send(req_obj, stream=True)

    if res.status_code != 200:
        error_body = await res.aread()
        await res.aclose()
        await client.aclose()
        raise HTTPException(
            status_code=res.status_code,
            detail=f"ElevenLabs Error ({res.status_code}): {error_body.decode(errors='ignore')}"
        )

    # 2. Yield audio chunks safely
    async def stream_generator():
        try:
            async for chunk in res.aiter_bytes():
                yield chunk
        finally:
            await res.aclose()
            await client.aclose()

    return StreamingResponse(
        stream_generator(),
        media_type="audio/mpeg"
    )