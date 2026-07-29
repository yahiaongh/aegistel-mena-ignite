import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../../.env"))
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))

from mem0 import Memory

config = {
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "url": os.getenv("QDRANT_URL"),
            "api_key": os.getenv("QDRANT_API_KEY"),
            "collection_name": "aegistel_memories_768",
            "embedding_model_dims": 768,
        }
    },
    "llm": {
        "provider": "groq",
        "config": {
            "api_key": os.getenv("GROQ_API_KEY"),
            "model": "llama-3.3-70b-versatile",
        }
    },
    "embedder": {
        "provider": "gemini",
        "config": {
            "api_key": os.getenv("GOOGLE_API_KEY"),
            "model": "gemini-embedding-001",
        }
    }
}

class NetworkMemoryEngine:
    def __init__(self):
        try:
            self.memory = Memory.from_config(config)
        except Exception as e:
            print(f"[MemoryEngine Warning] Init failed, falling back to local memory: {e}")
            self.memory = None
            self._local_store = []

    def retrieve_past_incidents(self, phone_number: str, query: str) -> list:
        user_id = phone_number.replace("+", "").strip()
        if self.memory:
            try:
                results = self.memory.search(query=query, filters={"user_id": user_id})
                return results.get("results", []) if isinstance(results, dict) else results
            except Exception as e:
                print(f"[Memory Engine Search Warning] {e}")
                return []
        else:
            return [m for m in self._local_store if m.get("user_id") == user_id]

    def store_security_event(self, phone_number: str, text: str, metadata: dict = None) -> str:
        user_id = phone_number.replace("+", "").strip()
        if self.memory:
            try:
                res = self.memory.add(text, user_id=user_id, metadata=metadata or {})
                return str(res)
            except Exception as e:
                print(f"[Memory Engine Add Warning] {e}")
                return "fallback_id"
        else:
            self._local_store.append({"user_id": user_id, "text": text, "metadata": metadata})
            return "local_fallback_id"

    def record_incident(self, phone_number: str, text: str, metadata: dict = None) -> str:
        return self.store_security_event(phone_number, text, metadata)

memory_engine = NetworkMemoryEngine()