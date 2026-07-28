from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.agents.graph_orchestrator import aegis_graph
import json

router = APIRouter()

@router.websocket("/ws/orchestrate")
async def websocket_orchestrate(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            payload = json.loads(data)
            
            initial_state = {
                "event_context": payload.get("event", "Default Transfer Check"),
                "phone_number": payload.get("phone_number", "+213550000000"),
                "security_clearance": False,
                "sim_swap_detected": False,
                "qod_slice_active": False,
                "risk_score": 0.0,
                "audit_memory_id": "",
                "decision": "PENDING",
                "trace_logs": []
            }
            
            # Execute LangGraph Multi-Agent Stack
            final_state = aegis_graph.invoke(initial_state)
            await websocket.send_json(final_state)
    except WebSocketDisconnect:
        pass