from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json

from .services.nac_service import nac_client
from .agents.telco_agent import run_agent, run_agent_stream

# app = FastAPI(
#     title="AegisTel - MENA Ignite Hackathon 2026",
#     description="Nokia Network-as-Code CAMARA orchestration + autonomous Groq agent",
#     version="1.0.0",
# )

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],  # fine for a demo; tighten to the real frontend origin before MWC Doha
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )


# @app.get("/")
# def read_root():
#     return {
#         "project": "AegisTel",
#         "hackathon": "GSMA MENA Ignite 2026",
#         "status": "ONLINE",
#         "camara_apis": ["sim_swap", "location_verification", "qod"],
#     }


# # ---------- Direct CAMARA endpoints (manual testing / dashboard tiles) ----------

# class SimSwapRequest(BaseModel):
#     phone_number: str
#     max_age_hours: int = 24


# @app.post("/api/v1/camara/sim-swap")
# async def check_sim_swap(req: SimSwapRequest):
#     result = await nac_client.check_sim_swap(req.phone_number, req.max_age_hours)
#     if result.get("status") == "EXCEPTION":
#         raise HTTPException(status_code=502, detail=result["error"])
#     return result


# class LocationVerifyRequest(BaseModel):
#     phone_number: str
#     latitude: float
#     longitude: float
#     radius_meters: int = 1000
#     max_age: int = 3600


# @app.post("/api/v1/camara/location-verify")
# async def verify_location(req: LocationVerifyRequest):
#     result = await nac_client.verify_location(
#         req.phone_number, req.latitude, req.longitude, req.radius_meters, req.max_age
#     )
#     if isinstance(result, dict) and result.get("status") == "EXCEPTION":
#         raise HTTPException(status_code=502, detail=result["error"])
#     return result


# class QodRequest(BaseModel):
#     phone_number: str
#     service_ip: str
#     qos_profile: str = "QOS_L"
#     duration_seconds: int = 3600


# @app.post("/api/v1/camara/qod")
# async def request_qod(req: QodRequest):
#     # NOTE: this creates a real, live sandbox session — session cleanup via the
#     # SDK's delete endpoint is unreliable (see earlier 403s), so sessions are
#     # left to self-expire via their duration_seconds rather than deleted here.
#     result = await nac_client.request_qod_session(
#         req.phone_number, req.service_ip, req.qos_profile, req.duration_seconds
#     )
#     if isinstance(result, dict) and result.get("status") == "EXCEPTION":
#         raise HTTPException(status_code=502, detail=result["error"])
#     return result


# # ---------- Autonomous AI agent ----------

# class AgentRequest(BaseModel):
#     event_description: str


# @app.post("/api/v1/agent/orchestrate")
# async def orchestrate(req: AgentRequest):
#     try:
#         decision = await run_agent(req.event_description)
#         return {"event": req.event_description, "decision": decision}
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))
app = FastAPI(
    title="AegisTel Core Engine",
    description="Autonomous Multi-API CAMARA Telecom Orchestrator",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AgentRequest(BaseModel):
    event_description: str

@app.get("/")
def read_root():
    return {
        "project": "AegisTel",
        "status": "ONLINE",
        "active_camara_apis": [
            "sim_swap", "location_verification", "qod",
            "number_verification", "congestion_insights", "device_reachability"
        ],
    }

@app.post("/api/v1/agent/orchestrate")
async def orchestrate(req: AgentRequest):
    try:
        decision = await run_agent(req.event_description)
        return {"event": req.event_description, "decision": decision}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.websocket("/ws/agent")
async def websocket_agent_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            payload = json.loads(data)
            event_text = payload.get("event_description", "")
            
            async for step in run_agent_stream(event_text):
                await websocket.send_text(json.dumps(step, default=str))
    except WebSocketDisconnect:
        pass