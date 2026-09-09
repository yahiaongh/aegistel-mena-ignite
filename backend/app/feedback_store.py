# backend/app/feedback_store.py
"""Lightweight JSONL feedback store for the live demo.

Visitors (judges, investors, operators, anyone) rate the product across
independent feature dimensions (1-5 stars), pick a quick mood, and may leave
optional text. The founder reads everything back through the admin-protected
route only. No database, no accounts: a single append-only file, scoped the
same way as the local memory store.
"""
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

FEATURE_FIELDS: Dict[str, str] = {
    "verdict": "Fraud verdict quality",
    "explainability": "Explains the WHY",
    "ui": "UI / experience",
    "voice": "Voice briefing",
    "drill": "Red-team drill",
    "performance": "Speed / responsiveness",
}
MOODS = ["😍", "😊", "😐", "😤"]
ROLES = ["judge", "investor", "operator", "user", "other"]

FEEDBACK_STORE_PATH = Path(
    os.environ.get(
        "AEGISTEL_FEEDBACK_PATH",
        str(Path(__file__).resolve().parents[1] / "data" / "feedback.jsonl"),
    )
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> List[Dict[str, Any]]:
    if not FEEDBACK_STORE_PATH.exists():
        return []
    records: List[Dict[str, Any]] = []
    try:
        with FEEDBACK_STORE_PATH.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return records


def sanitize_ratings(ratings: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """Keep only known feature keys with integer values in 1..5."""
    cleaned: Dict[str, int] = {}
    if not isinstance(ratings, dict):
        return cleaned
    for key, value in ratings.items():
        if key not in FEATURE_FIELDS:
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= parsed <= 5:
            cleaned[key] = parsed
    return cleaned


def submit_feedback(payload: Dict[str, Any], meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ratings = sanitize_ratings(payload.get("ratings"))
    mood = payload.get("mood") if payload.get("mood") in MOODS else None
    role = str(payload.get("role") or "").strip().lower()
    if role not in ROLES:
        role = "other"
    comment = str(payload.get("comment") or "").strip()[:2000]
    context = payload.get("context")
    if not isinstance(context, dict):
        context = {}

    record: Dict[str, Any] = {
        "id": uuid.uuid4().hex[:12],
        "created_at": _now(),
        "ratings": ratings,
        "mood": mood,
        "role": role,
        "comment": comment,
        "context": {k: v for k, v in context.items() if v is None or isinstance(v, (str, int, float, bool))},
    }
    if meta:
        record["meta"] = {k: v for k, v in meta.items() if v is None or isinstance(v, (str, int, float, bool))}

    FEEDBACK_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FEEDBACK_STORE_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return record


def list_feedback(limit: int = 200) -> List[Dict[str, Any]]:
    records = _load()
    records.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return records[: max(1, int(limit))]


def build_summary(records: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    items = records if records is not None else _load()
    per_feature: Dict[str, Dict[str, Any]] = {}
    for key, label in FEATURE_FIELDS.items():
        values = [int(r["ratings"][key]) for r in items if key in (r.get("ratings") or {})]
        per_feature[key] = {
            "field": label,
            "count": len(values),
            "avg": round(sum(values) / len(values), 2) if values else None,
            "distribution": {str(v): values.count(v) for v in sorted(set(values), reverse=True)} if values else {},
        }
    moods: Dict[str, int] = {}
    for r in items:
        mood = r.get("mood")
        if mood:
            moods[mood] = moods.get(mood, 0) + 1
    roles: Dict[str, int] = {}
    for r in items:
        role = r.get("role", "other")
        roles[role] = roles.get(role, 0) + 1
    return {
        "count": len(items),
        "per_feature": per_feature,
        "moods": moods,
        "roles": roles,
        "ratings_submitted": sum(len(r.get("ratings") or {}) for r in items),
    }