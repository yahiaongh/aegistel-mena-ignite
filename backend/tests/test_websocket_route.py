from fastapi.testclient import TestClient

from app.main import app


def test_websocket_orchestrate_route_is_registered() -> None:
    client = TestClient(app)

    with client.websocket_connect("/ws/orchestrate") as websocket:
        websocket.send_text('{"msisdn":"+9999123456","amount":120000}')
        first_message = websocket.receive_json()
        assert first_message["type"] == "connected"

        messages = []
        for _ in range(4):
            messages.append(websocket.receive_json())

        final_result = next((message for message in messages if message.get("type") == "final_result"), None)
        assert final_result is not None
        assert final_result["msisdn"] == "+9999123456"
        assert "risk_score" in final_result
