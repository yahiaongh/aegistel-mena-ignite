import os
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../../.env"))
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))

try:
    from mem0 import Memory
except Exception:
    Memory = None


class NetworkMemoryEngine:
    def __init__(self):
        self.memory = None
        self._local_store: List[Dict[str, Any]] = []
        if Memory is None:
            return
        try:
            config = {
                "vector_store": {
                    "provider": "qdrant",
                    "config": {
                        "url": os.getenv("QDRANT_URL"),
                        "api_key": os.getenv("QDRANT_API_KEY"),
                        "collection_name": "aegistel_memories_768",
                        "embedding_model_dims": 768,
                    },
                },
                "llm": {
                    "provider": "groq",
                    "config": {
                        "api_key": os.getenv("GROQ_API_KEY"),
                        "model": "llama-3.3-70b-versatile",
                    },
                },
                "embedder": {
                    "provider": "gemini",
                    "config": {
                        "api_key": os.getenv("GOOGLE_API_KEY"),
                        "model": "gemini-embedding-001",
                    },
                },
            }
            self.memory = Memory.from_config(config)
        except Exception as exc:
            print(f"[MemoryEngine Warning] Init failed, falling back to local memory: {exc}")

    def retrieve_past_incidents(self, phone_number: str, query: str) -> List[Dict[str, Any]]:
        user_id = phone_number.replace("+", "").strip()
        if self.memory:
            try:
                results = self.memory.search(query=query, filters={"user_id": user_id})
                return results.get("results", []) if isinstance(results, dict) else results
            except Exception as exc:
                print(f"[Memory Engine Search Warning] {exc}")
                return []
        return [m for m in self._local_store if m.get("user_id") == user_id]

    def store_security_event(self, phone_number: str, text: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        user_id = phone_number.replace("+", "").strip()
        if self.memory:
            try:
                res = self.memory.add(text, user_id=user_id, metadata=metadata or {})
                return str(res)
            except Exception as exc:
                print(f"[Memory Engine Add Warning] {exc}")
                return "fallback_id"
        self._local_store.append({"user_id": user_id, "text": text, "metadata": metadata or {}})
        return "local_fallback_id"

    def record_incident(self, phone_number: str, text: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        return self.store_security_event(phone_number, text, metadata)


memory_engine = NetworkMemoryEngine()