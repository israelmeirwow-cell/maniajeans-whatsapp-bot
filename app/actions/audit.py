"""Action audit trail — every state-changing operation the bot performs is recorded:
who (session), what, when, on which order, and the before/after states. Append-only."""
import json
import time
from typing import Dict, Any, List, Optional

from app.config import DATA_DIR

_LOG = DATA_DIR / "logs" / "actions.jsonl"


def record(session_id: str, action: str, target: str, result: str,
           before: Optional[str] = None, after: Optional[str] = None,
           detail: str = "") -> Dict[str, Any]:
    rec = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "session_id": session_id,
        "action": action,          # e.g. "cancel_order"
        "target": target,          # e.g. order id
        "result": result,          # "executed" | "refused_identity" | "refused_eligibility" | ...
        "before": before,
        "after": after,
        "detail": detail,
    }
    _LOG.parent.mkdir(parents=True, exist_ok=True)
    with _LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def list_actions(limit: int = 50) -> List[Dict[str, Any]]:
    if not _LOG.is_file():
        return []
    rows = [json.loads(l) for l in _LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    return list(reversed(rows))[:limit]


def executed_in_session(session_id: str, action: str) -> int:
    """How many times this session already executed this action (blast-radius limit)."""
    if not _LOG.is_file():
        return 0
    n = 0
    for l in _LOG.read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        try:
            r = json.loads(l)
        except json.JSONDecodeError:
            continue
        if r.get("session_id") == session_id and r.get("action") == action \
                and r.get("result") == "executed":
            n += 1
    return n
