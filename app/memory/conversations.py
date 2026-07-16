"""Per-customer conversation session (context) — so the bot never re-asks known details.

DEMO: in-memory store keyed by session id (the customer's WhatsApp number). Swap for
SQLite/Redis in production; the interface stays the same.

Load control (1,000+ conversations/month must not grow memory or token spend unbounded):
- the LLM receives only the last MAX_LLM_TURNS turns (facts/last_products survive anyway);
- idle sessions expire after SESSION_TTL and the store is capped at MAX_SESSIONS (LRU).
"""
import time
from typing import Dict, List, Any, Optional

MAX_LLM_TURNS = 30          # ≈15 exchanges — far beyond a typical support chat
SESSION_TTL = 12 * 3600     # idle sessions older than this are evicted
MAX_SESSIONS = 500          # hard cap; least-recently-active evicted first


class Session:
    def __init__(self, session_id: str):
        self.id = session_id
        self.turns: List[Dict[str, str]] = []      # [{"role": "user"/"assistant", "text": ...}]
        self.facts: Dict[str, Any] = {}            # e.g. {"name": "דני", "order_id": "100235"}
        self.tags: List[str] = []                  # intents seen (for logging/analytics)
        self.handoff: bool = False
        self.last_products: List[Dict[str, Any]] = []  # products shown last turn (for "the 2nd one")
        self.created_at = time.time()
        self.touched_at = time.time()

    def add_user(self, text: str) -> None:
        self.touched_at = time.time()
        self.turns.append({"role": "user", "text": text})

    def add_assistant(self, text: str) -> None:
        self.touched_at = time.time()
        self.turns.append({"role": "assistant", "text": text})

    def anthropic_messages(self, max_turns: int = MAX_LLM_TURNS) -> List[Dict[str, Any]]:
        """History as Anthropic messages (text-only turns), capped so a marathon chat
        doesn't inflate every call. Must start with a user turn (API requirement)."""
        turns = self.turns[-max_turns:] if max_turns else self.turns
        while turns and turns[0]["role"] != "user":
            turns = turns[1:]
        return [{"role": t["role"], "content": t["text"]} for t in turns]

    def set_fact(self, key: str, value: Any) -> None:
        if value:
            self.facts[key] = value

    def transcript(self) -> str:
        who = {"user": "לקוח", "assistant": "בוט"}
        return "\n".join(f"{who.get(t['role'], t['role'])}: {t['text']}" for t in self.turns)


class ConversationStore:
    def __init__(self):
        self._sessions: Dict[str, Session] = {}

    def _evict(self) -> None:
        now = time.time()
        stale = [sid for sid, s in self._sessions.items() if now - s.touched_at > SESSION_TTL]
        for sid in stale:
            self._sessions.pop(sid, None)
        if len(self._sessions) > MAX_SESSIONS:
            by_age = sorted(self._sessions.values(), key=lambda s: s.touched_at)
            for s in by_age[:len(self._sessions) - MAX_SESSIONS]:
                self._sessions.pop(s.id, None)

    def get(self, session_id: str) -> Session:
        if session_id not in self._sessions:
            self._evict()
            self._sessions[session_id] = Session(session_id)
        return self._sessions[session_id]

    def reset(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


store = ConversationStore()
