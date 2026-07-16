"""Conversation logging & tagging — auto-tag every message by its primary intent
for reporting and future improvement (CLAUDE.md §8)."""
import json
import time
from app.config import DATA_DIR

_LOG = DATA_DIR / "logs" / "conversations.jsonl"


def tag(session_id: str, intent: str, message: str, handoff: bool = False,
        sentiment: str = "calm") -> None:
    _LOG.parent.mkdir(parents=True, exist_ok=True)
    with _LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "session_id": session_id,
            "intent": intent,
            "handoff": handoff,
            "sentiment": sentiment,
            "message": (message or "")[:300],
        }, ensure_ascii=False) + "\n")
