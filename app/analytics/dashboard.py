"""Aggregations for the business dashboard — reads the jsonl logs and returns
everything the dashboard UI needs in one payload."""
import json
from collections import Counter
from typing import Dict, Any, List

from app.config import DATA_DIR
from app.woocommerce.client import store


def _read_jsonl(path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _api_cost() -> float:
    try:
        from app.analytics import costs
        return round(costs.total_usd(), 4)
    except Exception:
        return 0.0


INTENT_HE = {
    "product_info": "מידע על מוצרים",
    "order_status": "סטטוס הזמנה",
    "faq": "שאלות נפוצות",
    "cancel_order": "ביטול הזמנה",
    "refund_return": "החזר/פגם",
    "discount": "הנחות",
    "unrecognized": "לא מזוהה",
}


def snapshot() -> Dict[str, Any]:
    convs = _read_jsonl(DATA_DIR / "logs" / "conversations.jsonl")
    handoffs = _read_jsonl(DATA_DIR / "logs" / "handoffs.jsonl")
    tickets = _read_jsonl(DATA_DIR / "logs" / "tickets.jsonl")
    actions = _read_jsonl(DATA_DIR / "logs" / "actions.jsonl")
    lessons = _read_jsonl(DATA_DIR / "learning" / "lessons.jsonl")
    feedback = _read_jsonl(DATA_DIR / "learning" / "feedback.jsonl")

    sessions = {c.get("session_id") for c in convs}
    n_msgs = len(convs)
    n_handoff_msgs = sum(1 for c in convs if c.get("handoff"))

    intents = Counter(c.get("intent", "?") for c in convs)
    intent_rows = [{"intent": k, "label": INTENT_HE.get(k, k), "count": v}
                   for k, v in intents.most_common()]

    ratings = Counter(f.get("rating", "unknown") for f in feedback)

    # tickets can appear twice (append + rewrite on resolve) — dedupe by id, last wins
    tickets_by_id: Dict[str, Dict[str, Any]] = {}
    for t in tickets:
        tickets_by_id[t.get("id")] = t
    tlist = sorted(tickets_by_id.values(), key=lambda t: t.get("ts", ""), reverse=True)

    sim = {}
    sim_file = DATA_DIR / "logs" / "sim_status.json"
    if sim_file.is_file():
        try:
            sim = json.loads(sim_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            sim = {}

    return {
        "sim": sim,
        "kpis": {
            "conversations": len(sessions),
            "messages": n_msgs,
            "handoff_rate": round(100 * n_handoff_msgs / n_msgs, 1) if n_msgs else 0,
            "open_tickets": sum(1 for t in tlist if t.get("status") == "open"),
            "resolved_tickets": sum(1 for t in tlist if t.get("status") == "resolved"),
            "lessons": len(lessons),
            "api_cost_usd": _api_cost(),
            "actions_executed": sum(1 for a in actions if a.get("result") == "executed"),
            "satisfied": ratings.get("satisfied", 0),
            "dissatisfied": ratings.get("dissatisfied", 0),
            "catalog": store.stats(),
        },
        "intents": intent_rows,
        "tickets": tlist[:50],
        "lessons": list(reversed(lessons))[:20],
        "actions": [
            {"ts": a.get("ts"), "action": a.get("action"), "target": a.get("target"),
             "result": a.get("result"), "before": a.get("before"), "after": a.get("after")}
            for a in reversed(actions)
        ][:20],
        "handoffs": [
            {"ts": h.get("ts"), "intent": h.get("intent"), "reason": h.get("reason"),
             "session_id": h.get("session_id")}
            for h in reversed(handoffs)
        ][:15],
        "recent": [
            {"ts": c.get("ts"), "session_id": c.get("session_id"),
             "intent": INTENT_HE.get(c.get("intent"), c.get("intent")),
             "handoff": c.get("handoff", False), "sentiment": c.get("sentiment", "calm"),
             "message": c.get("message", "")}
            for c in reversed(convs)
        ][:30],
    }
