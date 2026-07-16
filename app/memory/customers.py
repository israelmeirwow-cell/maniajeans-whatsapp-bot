"""Long-term customer memory — persists ACROSS conversations, keyed by phone/session id.

A basic bot forgets everything when a chat ends. This gives the bot a durable profile per
customer: name, known facts, a one-line history of past conversations, and any open issue
(e.g. an escalation that wasn't resolved). On a returning customer the agent greets them by
name and can ask about the previous matter instead of starting cold.

DEMO: JSON file. Production: a DB row per customer (or the CRM).
"""
import json
import time
from typing import Dict, Any, Optional

from app.config import DATA_DIR

_FILE = DATA_DIR / "memory" / "customers.json"


def _load() -> Dict[str, Any]:
    if _FILE.is_file():
        try:
            return json.loads(_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save(data: Dict[str, Any]) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def get(customer_id: str) -> Optional[Dict[str, Any]]:
    return _load().get(customer_id)


def upsert(customer_id: str, name: Optional[str] = None, facts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = _load()
    rec = data.get(customer_id) or {
        "id": customer_id, "name": None, "facts": {}, "history": [],
        "open_issue": None, "first_seen": time.strftime("%Y-%m-%d"),
    }
    if name and not rec.get("name"):
        rec["name"] = name
    if facts:
        rec["facts"].update({k: v for k, v in facts.items() if v})
    rec["last_seen"] = time.strftime("%Y-%m-%d %H:%M")
    data[customer_id] = rec
    _save(data)
    return rec


def log_conversation(customer_id: str, summary: str, intents=None,
                     open_issue: Optional[str] = None) -> None:
    """Append a one-line record of a finished conversation; set/clear the open issue."""
    data = _load()
    rec = data.get(customer_id)
    if not rec:
        rec = upsert(customer_id)
        data = _load()
        rec = data[customer_id]
    rec.setdefault("history", []).insert(0, {
        "date": time.strftime("%Y-%m-%d %H:%M"),
        "summary": summary,
        "intents": list(intents or []),
    })
    rec["history"] = rec["history"][:10]
    rec["open_issue"] = open_issue
    rec["last_seen"] = time.strftime("%Y-%m-%d %H:%M")
    data[customer_id] = rec
    _save(data)


def context_note(customer_id: str) -> str:
    """A compact block injected into the system prompt for a RETURNING customer."""
    rec = get(customer_id)
    if not rec or (not rec.get("history") and not rec.get("open_issue")):
        return ""
    lines = ["## לקוח מוכר (זיכרון ארוך-טווח — התייחס בטבעיות, אל תקריא את זה כרשימה):"]
    if rec.get("name"):
        lines.append(f"- שם: {rec['name']}")
    if rec.get("open_issue"):
        lines.append(f"- **פנייה פתוחה מהעבר:** {rec['open_issue']} — פתח/י בשאלה אם זה הסתדר או שצריך עזרה חדשה.")
    elif rec.get("history"):
        last = rec["history"][0]
        lines.append(f"- פנייה קודמת ({last['date']}): {last['summary']}")
    return "\n".join(lines)
