import json
from typing import Any, Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.agents.graph_orchestrator import execute_audit
from app.schemas.telemetry import AuditRequest, LocationInput

router = APIRouter()


@router.websocket("/ws/orchestrate")
async def websocket_orchestrate(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            payload: Dict[str, Any] = json.loads(data)

            await websocket.send_json({"type": "connected", "message": "AegisTel telemetry stream started"})

            request = AuditRequest(
                msisdn=payload.get("msisdn") or payload.get("phone_number", "+213550000000"),
                amount=float(payload.get("amount", 50000)),
                transaction_type=payload.get("transaction_type", "WIRE_TRANSFER"),
                current_location=LocationInput(
                    latitude=float(payload.get("latitude", 24.7)),
                    longitude=float(payload.get("longitude", 46.7)),
                ),
                request_qod_slice=bool(payload.get("request_qod_slice", True)),
                metadata=payload.get("metadata", {}),
            )

            async def emit(event: Dict[str, Any]):
                await websocket.send_json({"type": "audit_event", **event})

            result = await execute_audit(request, stream=True, emit=emit)
            await websocket.send_json({"type": "final_result", **result.model_dump(mode="json")})
    except WebSocketDisconnect:
        pass