"""Service tickets — the enterprise 'callback commitment' pattern.

When the bot can't fully answer (a knowledge gap, a case-specific matter), instead of
dumping the customer on a live agent it OPENS A TICKET: captures the question + contact,
commits to a follow-up, and keeps the conversation resolved. The business answers the
ticket later; resolving a ticket can feed the learning loop so the NEXT customer gets
an instant answer (gap → ticket → human answer → lesson → bot knows it).

Tickets double as the knowledge-gap log (what customers ask that the bot can't answer).
"""
import json
import time
from typing import Dict, Any, List, Optional

from app.config import DATA_DIR

_FILE = DATA_DIR / "logs" / "tickets.jsonl"


def _load() -> List[Dict[str, Any]]:
    if not _FILE.is_file():
        return []
    out = []
    for line in _FILE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _save_all(rows: List[Dict[str, Any]]) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    _FILE.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def create(session_id: str, topic: str, question: str,
           customer_name: Optional[str] = None, phone: Optional[str] = None) -> Dict[str, Any]:
    rows = _load()
    ticket_id = f"T-{time.strftime('%Y%m%d')}-{len(rows) + 1:03d}"
    rec = {
        "id": ticket_id,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "session_id": session_id,
        "topic": topic,
        "question": question,
        "customer_name": customer_name,
        "phone": phone,
        "status": "open",
        "answer": None,
    }
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    with _FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def list_tickets(status: Optional[str] = None) -> List[Dict[str, Any]]:
    rows = _load()
    if status:
        rows = [r for r in rows if r.get("status") == status]
    return rows


def resolve(ticket_id: str, answer: str) -> Optional[Dict[str, Any]]:
    """Mark a ticket answered. Returns the updated ticket (caller may feed the answer
    into the learning loop via agent.record_correction)."""
    rows = _load()
    hit = None
    for r in rows:
        if r.get("id") == ticket_id:
            r["status"] = "resolved"
            r["answer"] = answer
            r["resolved_ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
            hit = r
            break
    if hit:
        _save_all(rows)
    return hit
