# backend/app/qdrant_store.py
"""Best-effort durable Qdrant mirror for audit history and visitor feedback.

The local JSONL files are the always-on store, but they sit on one disk. With
`AEGISTEL_LIVE_MEMORY=1` set and `QDRANT_URL` / `QDRANT_API_KEY` present, every
audit incident and feedback record is also mirrored to the Qdrant cloud
collection. On boot the memory engine and feedback store read both sources and
merge, so nothing important dies with a local restart or a wiped disk.

The store stays deterministic and dependency-light: exact-match payload
filtering only (msisdn / tenant / audit-id). No embeddings, no LLM extraction,
no local reranker that could hang a request. Every call is guarded so an
unreachable cluster degrades silently to the local JSONL store — the remote
tier is a durable mirror, never a hard dependency.
"""
import hashlib
import json
import logging
import os
import threading
import uuid
from typing import Any, Dict, List, Optional, Tuple

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PointStruct,
    VectorParams,
)

from app.core.config import settings

logger = logging.getLogger(__name__)

EMPTY_VECTOR = [0.0] * 4

AUDIT_COLLECTION = "aegistel_audit_history"
FEEDBACK_COLLECTION = "aegistel_feedback"


def audit_point_id(record: Dict[str, Any]) -> str:
    """Deterministic point id for an audit record (idempotent double-writes)."""
    key = "|".join(
        [
            str(record.get("created_at") or ""),
            str(record.get("user_id") or ""),
            str(record.get("text") or ""),
            json.dumps(record.get("metadata") or {}, sort_keys=True, default=str),
        ]
    )
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def _flatten(record: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy with `tenant_id` moved to the payload root for filtering."""
    out = dict(record)
    meta = record.get("metadata") or {}
    out["tenant_id"] = str(meta.get("tenant_id", "demo"))
    return out


class QdrantStore:
    """Thin wrapper over one Qdrant collection, safe to construct anywhere.

    Construction does no network I/O; the client and collection are created
    lazily on the first operation and only when the deployment opts in. A store
    can be hard-pinned with `enabled=False` (tests) to keep it inert no matter
    what the environment says.
    """

    def __init__(self, collection_name: str, *, enabled: Optional[bool] = None) -> None:
        self._collection = collection_name
        self._force = enabled
        self._client: Any = None
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        if self._force is not None:
            return self._force
        return (
            os.environ.get("AEGISTEL_LIVE_MEMORY", "0") == "1"
            and bool(settings.QDRANT_URL)
            and bool(settings.QDRANT_API_KEY)
        )

    @property
    def collection(self) -> str:
        return self._collection

    def _get_client(self) -> Any:
        if not self.enabled:
            return None
        if self._client is None:
            with self._lock:
                if self._client is None:
                    client = QdrantClient(
                        url=settings.QDRANT_URL,
                        api_key=settings.QDRANT_API_KEY,
                        timeout=5,
                    )
                    if not client.collection_exists(self._collection):
                        try:
                            client.create_collection(
                                collection_name=self._collection,
                                vectors_config=VectorParams(
                                    size=len(EMPTY_VECTOR), distance=Distance.DOT
                                ),
                                on_disk_payload=True,
                            )
                        except Exception as exc:  # noqa: BLE001 - remote tier degradation
                            logger.warning(
                                "[QdrantStore] create_collection('%s') failed: %s",
                                self._collection,
                                exc,
                            )
                    # Payload-filtered scrolls/counts need a field index on the
                    # filtered key ("Index required but not found for
                    # 'user_id'…" — a hard 400 on Qdrant cloud). Without these
                    # indexes a cold start on wiped disk can't restore the
                    # per-subscriber mirror, so list_by silently returns [].
                    for field in ("user_id", "tenant_id", "created_at"):
                        try:
                            client.create_payload_index(
                                collection_name=self._collection,
                                field_name=field,
                                field_schema="keyword",
                            )
                        except Exception as exc:  # noqa: BLE001 - best-effort
                            logger.warning(
                                "[QdrantStore] payload index '%s' not created on '%s': %s",
                                field,
                                self._collection,
                                exc,
                            )
                    self._client = client
        return self._client

    def _filter(self, match: Optional[Dict[str, str]]) -> Any:
        if not match:
            return Filter()
        return Filter(
            must=[
                FieldCondition(key=str(k), match=MatchValue(value=str(v)))
                for k, v in match.items()
                if v is not None
            ]
        )

    @staticmethod
    def _normalize_point_id(point_id: Any) -> str:
        """Qdrant only accepts unsigned integers or UUIDs as point ids."""
        pid = str(point_id)
        try:
            uuid.UUID(pid)
            return pid
        except (ValueError, AttributeError):
            pass
        try:
            int(pid)
            return pid
        except ValueError:
            pass
        return str(uuid.uuid5(uuid.NAMESPACE_URL, pid))

    def upsert(self, point_id: str, record: Dict[str, Any]) -> bool:
        """Best-effort append/replace of one record. Returns True on success."""
        try:
            client = self._get_client()
            if client is None:
                return False
            client.upsert(
                collection_name=self._collection,
                points=[
                    PointStruct(
                        id=self._normalize_point_id(point_id),
                        vector=list(EMPTY_VECTOR),
                        payload=_flatten(record),
                    )
                ],
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[QdrantStore.%s] upsert failed: %s", self._collection, exc)
            return False

    def list_all(self, limit: int = 20000) -> List[Dict[str, Any]]:
        return self._scroll(self._filter(None), limit=limit)

    def list_by(
        self,
        match: Optional[Dict[str, str]] = None,
        *,
        date_key: str = "created_at",
        reverse: bool = False,
        limit: int = 10000,
    ) -> List[Dict[str, Any]]:
        """Filter by exact-match payload, then sort client-side by `date_key`."""
        records = self._scroll(self._filter(match), limit=limit)
        records.sort(key=lambda r: str(r.get(date_key) or ""), reverse=reverse)
        return records

    def _scroll(self, filter_obj: Any, limit: int) -> List[Dict[str, Any]]:
        try:
            client = self._get_client()
            if client is None:
                return []
            records: List[Dict[str, Any]] = []
            offset: Any = None
            while True:
                page, offset = client.scroll(
                    collection_name=self._collection,
                    scroll_filter=filter_obj,
                    limit=1000,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                for rec in page or []:
                    if rec.payload:
                        records.append(dict(rec.payload))
                    if len(records) >= limit:
                        return records[:limit]
                if offset is None:
                    return records[:limit]
        except Exception as exc:  # noqa: BLE001
            logger.warning("[QdrantStore.%s] scroll failed: %s", self._collection, exc)
            return []

    def delete_by(self, match: Optional[Dict[str, str]] = None) -> int:
        try:
            client = self._get_client()
            if client is None:
                return 0
            client.delete(
                collection_name=self._collection,
                points_selector=FilterSelector(filter=self._filter(match)),
            )
            return 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("[QdrantStore.%s] delete_by failed: %s", self._collection, exc)
            return 0

    def delete_all(self) -> int:
        return self.delete_by(None)

    def count(self, match: Optional[Dict[str, str]] = None) -> int:
        try:
            client = self._get_client()
            if client is None:
                return 0
            result = client.count(
                collection_name=self._collection,
                count_filter=self._filter(match),
                exact=True,
            )
            return int(result.count)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[QdrantStore.%s] count failed: %s", self._collection, exc)
            return 0