from mem0 import Memory
from app.core.config import settings

class NetworkMemoryEngine:
    def __init__(self):
        config = {
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "url": settings.QDRANT_URL,
                    "api_key": settings.QDRANT_API_KEY,
                }
            }
        }
        self.memory = Memory.from_config(config)

    def store_security_event(self, phone_number: str, text: str, metadata: dict):
        self.memory.add(text, user_id=phone_number, metadata=metadata)

    def retrieve_past_incidents(self, phone_number: str, query: str):
        return self.memory.search(query, user_id=phone_number, limit=3)

memory_engine = NetworkMemoryEngine()