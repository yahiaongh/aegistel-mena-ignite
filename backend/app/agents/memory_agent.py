# backend/app/agents/memory_agent.py
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config import settings

try:
    from mem0 import Memory
except Exception:
    Memory = None

logger = logging.getLogger(__name__)
LOCAL_STORE_PATH = Path(__file__).resolve().parents[2] / "data" / "local_memory.jsonl"


class NetworkMemoryEngine:
    def __init__(self):
        self.memory = None
        self._local_store: List[Dict[str, Any]] = self._load_local_store()
        if Memory is None:
            return
        try:
            config = {
                "vector_store": {
                    "provider": "qdrant",
                    "config": {
                        "url": settings.QDRANT_URL,
                        "api_key": settings.QDRANT_API_KEY,
                        "collection_name": "aegistel_memories_768",
                        "embedding_model_dims": 768,
                    },
                },
                "llm": {
                    "provider": "gemini",
                    "config": {
                        "api_key": settings.GOOGLE_API_KEY,
                        "model": settings.GEMINI_MODEL,
                    },
                },
                "embedder": {
                    "provider": "gemini",
                    "config": {
                        "api_key": settings.GOOGLE_API_KEY,
                        "model": "gemini-embedding-001",
                    },
                },
            }
            self.memory = Memory.from_config(config)
        except Exception as exc:
            print(f"[MemoryEngine Warning] Init failed, falling back to local memory: {exc}")

    def _load_local_store(self) -> List[Dict[str, Any]]:
        if not LOCAL_STORE_PATH.exists():
            return []
        try:
            with LOCAL_STORE_PATH.open("r", encoding="utf-8") as handle:
                return [json.loads(line) for line in handle if line.strip()]
        except Exception as exc:
            logger.warning("Failed to load local memory store: %s", exc)
            return []

    def _append_local_store(self, record: Dict[str, Any]) -> None:
        LOCAL_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            with LOCAL_STORE_PATH.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, default=str) + "\n")
        except Exception as exc:
            logger.warning("Failed to persist local memory record: %s", exc)

    def retrieve_past_incidents(self, phone_number: str, query: str) -> List[Dict[str, Any]]:
        user_id = phone_number.replace("+", "").strip()

        if self.memory:
            # Round 12 safe mode: mem0ai 2.0.5 may load a local HuggingFace reranker / torch
            # during `.search(...)` even when configured for Gemini embeddings. This path can
            # hang in demo-critical scenarios, so use the local fallback memory only.
            logger.warning(
                "[Round12] Skipping mem0 live search due to potential local torch/reranker load; using local fallback memory."
            )
            return [m for m in self._local_store if m.get("user_id") == user_id]

        return [m for m in self._local_store if m.get("user_id") == user_id]

    async def retrieve_past_incidents_async(self, phone_number: str, query: str) -> List[Dict[str, Any]]:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self.retrieve_past_incidents, phone_number, query),
                timeout=8,
            )
        except asyncio.TimeoutError:
            logger.warning("Memory search exceeded 8s timeout; continuing without memory context")
            return []

    def clear_all_memory(self) -> None:
        """Wipes all records from Mem0 storage and resets local fallback memory."""
        if self.memory:
            try:
                self.memory.delete_all(user_id="*", agent_id="*", run_id="*")
            except Exception as exc:
                print(f"[Memory Engine Clear Warning] {exc}")
        self._local_store.clear()
        try:
            if LOCAL_STORE_PATH.exists():
                LOCAL_STORE_PATH.unlink()
        except Exception as exc:
            logger.warning("Failed to clear persisted local memory file: %s", exc)

    def list_all_incidents(self, phone_number: Optional[str] = None) -> List[Dict[str, Any]]:
        if phone_number is None:
            return list(self._local_store)
        user_id = phone_number.replace("+", "").strip()
        return [m for m in self._local_store if m.get("user_id") == user_id]

    def store_security_event(self, phone_number: str, text: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        user_id = phone_number.replace("+", "").strip()
        created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        record = {
            "user_id": user_id,
            "text": text,
            "metadata": metadata or {},
            "created_at": created_at,
        }
        self._local_store.append(record)
        self._append_local_store(record)

        if self.memory:
            try:
                res = self.memory.add(text, user_id=user_id, metadata=metadata or {})
                return str(res)
            except Exception as exc:
                print(f"[Memory Engine Add Warning] {exc}")
                return "fallback_id"
        return "local_fallback_id"

    def record_incident(self, phone_number: str, text: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        return self.store_security_event(phone_number, text, metadata)


memory_engine = NetworkMemoryEngine()
