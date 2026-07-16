"""Human handover — builds a structured summary and 'notifies' a human agent.

DEMO: the notification is appended to data/logs/handoffs.jsonl and printed. In
production this would ping the agent's channel (WhatsApp/email/ticket system) via
settings.HANDOFF_NOTIFY.
"""
import json
import time
from pathlib import Path
from typing import Optional

from app.config import DATA_DIR, settings
from app.memory.conversations import Session

_LOG = DATA_DIR / "logs" / "handoffs.jsonl"


def build_summary(session: Session, intent: str, reason: str) -> str:
    facts = session.facts or {}
    ident = facts.get("name") or "לא ידוע"
    order = facts.get("order_id") or "—"
    lines = [
        "🔔 העברה לנציג אנושי",
        f"לקוח: {ident}  |  מזהה שיחה: {session.id}",
        f"כוונה שזוהתה: {intent}",
        f"מספר הזמנה (אם נמסר): {order}",
        f"סיבת ההעברה: {reason}",
        "",
        "סיכום השיחה עד כה:",
        session.transcript() or "(אין הודעות)",
    ]
    return "\n".join(lines)


def escalate(session: Session, intent: str, reason: str) -> str:
    """Mark handoff, persist a structured summary, 'notify' the agent. Returns the summary."""
    session.handoff = True
    summary = build_summary(session, intent, reason)
    _LOG.parent.mkdir(parents=True, exist_ok=True)
    with _LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "session_id": session.id,
            "intent": intent,
            "reason": reason,
            "notify": settings.HANDOFF_NOTIFY,
            "summary": summary,
        }, ensure_ascii=False) + "\n")
    # Real-time alert to the human agent's WhatsApp (no-op until the gateway is configured).
    if settings.HANDOFF_NOTIFY:
        from app.whatsapp import gateway
        gateway.notify_agent(summary)
    return summary
