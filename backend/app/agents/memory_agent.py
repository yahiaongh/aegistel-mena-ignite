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
LOCAL_STORE_PATH = Path(
    os.environ.get(
        "AEGISTEL_MEMORY_PATH",
        str(Path(__file__).resolve().parents[2] / "data" / "local_memory.jsonl"),
    )
)


class NetworkMemoryEngine:
    def __init__(self):
        self.memory = None
        self._local_store: List[Dict[str, Any]] = self._load_local_store()
        if Memory is None:
            return
        if os.environ.get("AEGISTEL_LIVE_MEMORY", "0") != "1":
            # Remote mem0/Qdrant/Gemini memory is only constructed when the
            # deployment explicitly opts in (AEGISTEL_LIVE_MEMORY=1). The demo
            # and test suite run entirely on the deterministic local store, so
            # no network-dependent client is ever built without intent, and
            # requests never hang on remote memory initialization.
            return
        try:
            # mem0's LLM layer performs extraction/synthesis (one Gemini request
            # per memory op). Gemini's free tier is per-model and tiny (~20 RPD
            # on flash), and the crew needs that quota — so route mem0's LLM to
            # Groq (openai/gpt-oss-20b, the volume tier that replaced the
            # decommissioned llama-3.1-8b-instant), keeping Gemini only as
            # fallback. Embeddings stay on Gemini (separate, generous quota).
            if settings.GROQ_API_KEY:
                llm_config = {
                    "provider": "groq",
                    "config": {
                        "api_key": settings.GROQ_API_KEY,
                        "model": "openai/gpt-oss-20b",
                    },
                }
            else:
                llm_config = {
                    "provider": "gemini",
                    "config": {
                        "api_key": settings.GOOGLE_API_KEY,
                        "model": settings.GEMINI_MODEL,
                    },
                }
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
                "llm": llm_config,
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

    @staticmethod
    def _tenant_matches(record: Dict[str, Any], tenant_id: Optional[str]) -> bool:
        if tenant_id is None:
            return True
        meta = record.get("metadata") or {}
        return isinstance(meta, dict) and str(meta.get("tenant_id", "demo")).lower() == tenant_id.lower()

    def retrieve_past_incidents(
        self, phone_number: str, query: str, tenant_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        user_id = phone_number.replace("+", "").strip()

        if self.memory:
            # Round 12 safe mode: mem0ai 2.0.5 may load a local HuggingFace reranker / torch
            # during `.search(...)` even when configured for Gemini embeddings. This path can
            # hang in demo-critical scenarios, so use the local fallback memory only.
            logger.warning(
                "[Round12] Skipping mem0 live search due to potential local torch/reranker load; using local fallback memory."
            )
            return [m for m in self._local_store if m.get("user_id") == user_id and self._tenant_matches(m, tenant_id)]

        return [m for m in self._local_store if m.get("user_id") == user_id and self._tenant_matches(m, tenant_id)]

    async def retrieve_past_incidents_async(
        self, phone_number: str, query: str, tenant_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self.retrieve_past_incidents, phone_number, query, tenant_id),
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

    def clear_tenant_memory(self, tenant_id: str) -> Dict[str, Any]:
        """Clear memory records scoped to a specific tenant.

        For local store: filters out records with matching tenant_id in metadata.
        For remote Mem0: attempts best-effort deletion by listing and deleting
        individual memories with the tenant_id in metadata.
        """
        cleared_local = 0
        if self._local_store:
            before = len(self._local_store)
            self._local_store = [
                m for m in self._local_store
                if not self._tenant_matches(m, tenant_id)
            ]
            cleared_local = before - len(self._local_store)
            # Re-persist
            try:
                with LOCAL_STORE_PATH.open("w", encoding="utf-8") as f:
                    for rec in self._local_store:
                        f.write(json.dumps(rec) + "\n")
            except Exception as exc:
                logger.warning("Failed to persist local memory after tenant clear: %s", exc)

        cleared_remote = 0
        if self.memory:
            try:
                # Search for memories with this tenant_id - use a broad query
                # Note: mem0 doesn't support metadata filtering in delete_all,
                # so we search then delete individually.
                results = self.memory.search(
                    query="", user_id="*", limit=1000
                )
                for mem in results or []:
                    meta = mem.get("metadata") or {}
                    if str(meta.get("tenant_id", "")).lower() == tenant_id.lower():
                        mem_id = mem.get("id") or mem.get("memory_id")
                        if mem_id:
                            try:
                                self.memory.delete(memory_id=mem_id)
                                cleared_remote += 1
                            except Exception:
                                pass
            except Exception as exc:
                logger.warning("Failed to clear remote tenant memory for %s: %s", tenant_id, exc)

        return {
            "tenant_id": tenant_id,
            "cleared_local": cleared_local,
            "cleared_remote": cleared_remote,
            "note": "Remote Mem0 tenant clear is best-effort; full remote wipe requires superadmin.",
        }

    def list_all_incidents(
        self, phone_number: Optional[str] = None, tenant_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        base = list(self._local_store)
        if phone_number is not None:
            user_id = phone_number.replace("+", "").strip()
            base = [m for m in base if m.get("user_id") == user_id]
        if tenant_id is not None:
            base = [m for m in base if self._tenant_matches(m, tenant_id)]
        return base

    def find_audit_decision(self, audit_id: str, phone_number: str, tenant_id: str) -> Optional[Dict[str, Any]]:
        """Return a tenant-scoped audit decision record by immutable decision id.

        QoD provisioning is allowed only from a fresh risk decision, never from
        a bare subscriber number. The local store is append-only, so scan from
        newest to oldest and require the MSISDN and tenant to match exactly.
        Only match actual audit decisions (not QOD_PROVISIONED records which
        share the same audit_id but lack qod_recommended metadata).
        """
        DECISION_STATUSES = {"APPROVED", "STEP_UP_REQUIRED", "BLOCKED", "REJECTED", "MANUAL_REVIEW"}
        for record in reversed(self.list_all_incidents(phone_number, tenant_id=tenant_id)):
            metadata = record.get("metadata") or {}
            if (
                isinstance(metadata, dict)
                and str(metadata.get("audit_id", "")) == audit_id
                and metadata.get("status") in DECISION_STATUSES
            ):
                return record
        return None

    def has_qod_provisioning(self, audit_id: str, phone_number: str, tenant_id: str) -> bool:
        """Return whether this tenant has already consumed an audit's QoD approval."""
        for record in self.list_all_incidents(phone_number, tenant_id=tenant_id):
            metadata = record.get("metadata") or {}
            if (
                isinstance(metadata, dict)
                and metadata.get("status") == "QOD_PROVISIONED"
                and str(metadata.get("audit_id", "")) == audit_id
            ):
                return True
        return False

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
