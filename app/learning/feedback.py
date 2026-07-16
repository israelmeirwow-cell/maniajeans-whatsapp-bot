"""Satisfaction assessment — reads a finished conversation and judges whether the
customer was helped, using the cheap classifier model. Logs the signal for review
and flags conversations worth learning from.

Satisfaction here is INFERRED from the transcript (implicit). You can also feed an
explicit signal (a 👍/👎 the customer sent) via `explicit`.
"""
import re
import json
import time
from typing import Dict, Any, Optional

from app.config import settings, DATA_DIR

_LOG = DATA_DIR / "learning" / "feedback.jsonl"

_SYSTEM = (
    "אתה מעריך/ה שביעות רצון של לקוחות בשיחות שירות בעברית. קרא/י את השיחה והחזר/י JSON בלבד: "
    '{"rating": "satisfied|neutral|dissatisfied", "reason": "משפט קצר", "lesson_worthy": true|false}. '
    "dissatisfied = הלקוח לא קיבל מענה, התוסכל, חזר על שאלה שוב, או נאלץ לעבור לנציג בגלל כשל של הבוט. "
    "lesson_worthy=true אם יש כאן טעות או פער-ידע שאפשר ללמוד ממנו כדי לשפר תשובות עתידיות."
)


def assess(session, client=None, explicit: Optional[str] = None) -> Dict[str, Any]:
    """Judge the conversation. `explicit` may be 'up'/'down' from a customer rating."""
    if explicit in ("up", "down"):
        data = {"rating": "satisfied" if explicit == "up" else "dissatisfied",
                "reason": "explicit customer signal", "lesson_worthy": explicit == "down"}
    elif client is None or not settings.has_claude:
        data = {"rating": "unknown", "reason": "no LLM available", "lesson_worthy": False}
    else:
        try:
            resp = client.messages.create(
                model=settings.CLAUDE_CLASSIFIER_MODEL, max_tokens=200,
                system=_SYSTEM, messages=[{"role": "user", "content": session.transcript()}])
            from app.analytics import costs
            costs.record_usage(settings.CLAUDE_CLASSIFIER_MODEL, getattr(resp, "usage", None))
            txt = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            m = re.search(r"\{.*\}", txt, re.S)
            data = json.loads(m.group(0)) if m else {"rating": "unknown", "lesson_worthy": False}
        except Exception as e:
            data = {"rating": "unknown", "reason": str(e)[:80], "lesson_worthy": False}

    data["session_id"] = session.id
    data["intents"] = list(session.tags)
    data["handoff"] = session.handoff
    _log(data)
    return data


def _log(d: Dict[str, Any]) -> None:
    _LOG.parent.mkdir(parents=True, exist_ok=True)
    with _LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **d}, ensure_ascii=False) + "\n")


def report() -> Dict[str, Any]:
    """Aggregate satisfaction signals for review (per-intent failure patterns)."""
    if not _LOG.is_file():
        return {"total": 0}
    rows = [json.loads(l) for l in _LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    counts = {"satisfied": 0, "neutral": 0, "dissatisfied": 0, "unknown": 0}
    by_intent: Dict[str, Dict[str, int]] = {}
    for r in rows:
        counts[r.get("rating", "unknown")] = counts.get(r.get("rating", "unknown"), 0) + 1
        for it in r.get("intents", []):
            d = by_intent.setdefault(it, {"total": 0, "dissatisfied": 0})
            d["total"] += 1
            if r.get("rating") == "dissatisfied":
                d["dissatisfied"] += 1
    return {"total": len(rows), "ratings": counts, "by_intent": by_intent}
