# backend/app/agents/memory_agent.py
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.qdrant_store import QdrantStore, audit_point_id

logger = logging.getLogger(__name__)
LOCAL_STORE_PATH = Path(
    os.environ.get(
        "AEGISTEL_MEMORY_PATH",
        str(Path(__file__).resolve().parents[2] / "data" / "local_memory.jsonl"),
    )
)


class NetworkMemoryEngine:
    """Append-only audit history with a durable second copy in Qdrant.

    The local JSONL file is the always-on store (append-ordered, oldest first).
    When the deployment opts in (`AEGISTEL_LIVE_MEMORY=1`) with Qdrant
    configured, every incident is ALSO mirrored to the `aegistel_audit_history`
    Qdrant collection. Reads merge both stores and de-duplicate, so a cold start
    on an empty disk still restores the full history from Qdrant. The remote
    tier is best-effort: an unreachable cluster degrades silently to local.
    """

    def __init__(self):
        self._local_store: List[Dict[str, Any]] = self._load_local_store()
        self.qdrant = QdrantStore("aegistel_audit_history")

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

    def _remote_incidents(
        self, user_id: Optional[str], tenant_id: Optional[str]
    ) -> List[Dict[str, Any]]:
        if not self.qdrant.enabled:
            return []
        match: Dict[str, str] = {}
        if user_id is not None:
            match["user_id"] = user_id
        if tenant_id is not None:
            match["tenant_id"] = tenant_id
        return self.qdrant.list_by(match or None, date_key="created_at", reverse=False)

    def _merge_with_remote(
        self,
        local: List[Dict[str, Any]],
        user_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """De-duplicate local + Qdrant records, keeping local append order.

        The remote pull honours the same subscriber/tenant scope already applied
        to `local`, so a tenant-scoped read can never leak another tenant's
        (or another subscriber's) records through the mirror.
        """
        merged: List[Dict[str, Any]] = []
        seen = set()
        for rec in list(local):
            key = audit_point_id(rec)
            if key in seen:
                continue
            seen.add(key)
            merged.append(rec)
        for rec in self._remote_incidents(user_id, tenant_id):
            key = audit_point_id(rec)
            if key in seen:
                continue
            seen.add(key)
            merged.append(rec)
        return merged

    def retrieve_past_incidents(
        self, phone_number: str, query: str, tenant_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Full incident trail for a subscriber, from local JSONL + Qdrant."""
        return self.list_all_incidents(phone_number, tenant_id=tenant_id)

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
        """Wipes every record: local fallback file AND the Qdrant mirror."""
        self._local_store.clear()
        try:
            if LOCAL_STORE_PATH.exists():
                LOCAL_STORE_PATH.unlink()
        except Exception as exc:
            logger.warning("Failed to clear persisted local memory file: %s", exc)
        if self.qdrant.enabled:
            try:
                self.qdrant.delete_all()
            except Exception as exc:
                logger.warning("Failed to clear Qdrant memory mirror: %s", exc)

    def clear_tenant_memory(self, tenant_id: str) -> Dict[str, Any]:
        """Clear memory records scoped to a specific tenant (local + Qdrant)."""
        cleared_local = 0
        if self._local_store:
            before = len(self._local_store)
            self._local_store = [
                m for m in self._local_store
                if not self._tenant_matches(m, tenant_id)
            ]
            cleared_local = before - len(self._local_store)
            try:
                with LOCAL_STORE_PATH.open("w", encoding="utf-8") as f:
                    for rec in self._local_store:
                        f.write(json.dumps(rec) + "\n")
            except Exception as exc:
                logger.warning("Failed to persist local memory after tenant clear: %s", exc)

        cleared_remote = 0
        if self.qdrant.enabled:
            try:
                cleared_remote = 1 if self.qdrant.delete_by({"tenant_id": tenant_id}) else 0
            except Exception as exc:
                logger.warning("Failed to clear Qdrant tenant memory for %s: %s", tenant_id, exc)

        return {
            "tenant_id": tenant_id,
            "cleared_local": cleared_local,
            "cleared_remote": cleared_remote,
            "note": "Local cleared by scan; Qdrant mirror cleared by tenant filter.",
        }

    def list_all_incidents(
        self, phone_number: Optional[str] = None, tenant_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        user_id = None
        base = list(self._local_store)
        if phone_number is not None:
            user_id = phone_number.replace("+", "").strip()
            base = [m for m in base if m.get("user_id") == user_id]
        if tenant_id is not None:
            base = [m for m in base if self._tenant_matches(m, tenant_id)]
        return self._merge_with_remote(base, user_id=user_id, tenant_id=tenant_id)

    def find_audit_decision(self, audit_id: str, phone_number: str, tenant_id: str) -> Optional[Dict[str, Any]]:
        """Return a tenant-scoped audit decision by immutable decision id.

        QoD provisioning is allowed only from a fresh risk decision, never from
        a bare subscriber number. The local store is append-only, so scan newest
        to oldest and require the MSISDN and tenant to match exactly. Match only
        actual audit decisions, not QOD_PROVISIONED records that share the
        audit_id but lack qod_recommended metadata.
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
        if self.qdrant.enabled:
            self.qdrant.upsert(audit_point_id(record), record)
        return "local_fallback_id"

    def record_incident(self, phone_number: str, text: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        return self.store_security_event(phone_number, text, metadata)


memory_engine = NetworkMemoryEngine()