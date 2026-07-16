"""Order actions — the bot's WRITE capabilities, wrapped in safety layers.

Safety model (enterprise pattern, prepare → confirm → execute):
  1. Identity verification — the phone supplied must match the phone on the order.
  2. Eligibility check     — only orders still in processing ("בטיפול") are auto-cancelable;
                             shipped/ready/late/lost orders go to a human.
  3. Two-step confirmation — first call (confirm=False) only STAGES the action and returns
                             a summary; execution requires a second call with confirm=True,
                             after the customer explicitly said yes.
  4. Blast-radius limit    — at most one executed cancellation per conversation.
  5. Audit trail           — every attempt (refused or executed) is recorded.

DEMO: writes to data/store/demo_orders.json. In production this maps to the
WooCommerce orders API (a scoped write credential for order status only).
"""
import json
from typing import Dict, Any, Optional

from app.config import STORE_DIR
from app.woocommerce.client import store, _digits
from app.actions import audit

CANCELABLE_STATUSES = {"בטיפול"}
_ORDERS_FILE = STORE_DIR / "demo_orders.json"


def _persist_status(order_id: str, new_status: str) -> None:
    orders = json.loads(_ORDERS_FILE.read_text(encoding="utf-8"))
    for o in orders:
        if str(o.get("order_id")) == str(order_id):
            o["status"] = new_status
    _ORDERS_FILE.write_text(json.dumps(orders, ensure_ascii=False, indent=2), encoding="utf-8")
    # keep the in-memory snapshot in sync
    for o in store._orders:
        if str(o.get("order_id")) == str(order_id):
            o["status"] = new_status


def cancel_order(session_id: str, order_id: str, phone: str,
                 confirm: bool = False) -> Dict[str, Any]:
    """Guarded cancellation. Returns a dict the model relays to the customer."""
    order = next((o for o in store._orders if str(o.get("order_id")) == str(order_id).strip()), None)

    # --- layer 0: order exists ---
    if not order:
        audit.record(session_id, "cancel_order", order_id, "refused_not_found")
        return {"ok": False, "stage": "refused",
                "reason": "לא נמצאה הזמנה עם המספר הזה. בקש/י מהלקוח לוודא את מספר ההזמנה."}

    # --- layer 1: identity — phone must match the order ---
    if not phone or _digits(phone) != _digits(order.get("phone")):
        audit.record(session_id, "cancel_order", order_id, "refused_identity")
        return {"ok": False, "stage": "refused",
                "reason": "אימות נכשל: מספר הטלפון לא תואם את ההזמנה. מטעמי אבטחה אי אפשר לבטל. "
                          "בקש/י מהלקוח את הטלפון שאיתו בוצעה ההזמנה. אחרי שני כשלונות — העבר/י לנציג."}

    # --- layer 2: eligibility — only orders still in processing ---
    status = order.get("status", "")
    if status not in CANCELABLE_STATUSES:
        audit.record(session_id, "cancel_order", order_id, "refused_eligibility", before=status)
        return {"ok": False, "stage": "refused",
                "reason": f"ההזמנה בסטטוס '{status}' ולא ניתנת לביטול אוטומטי "
                          f"(רק הזמנות בסטטוס 'בטיפול'). העבר/י לנציג אנושי שיטפל בביטול ידני."}

    # --- layer 3: blast radius — one cancellation per conversation ---
    if audit.executed_in_session(session_id, "cancel_order") >= 1:
        audit.record(session_id, "cancel_order", order_id, "refused_rate_limit")
        return {"ok": False, "stage": "refused",
                "reason": "כבר בוצע ביטול בשיחה הזו. ביטול נוסף מחייב נציג אנושי."}

    # --- layer 4: two-step confirmation ---
    if not confirm:
        audit.record(session_id, "cancel_order", order_id, "staged", before=status)
        return {"ok": True, "stage": "needs_confirmation",
                "summary": {
                    "order_id": order.get("order_id"),
                    "customer_name": order.get("customer_name"),
                    "items": order.get("items", []),
                    "total": order.get("total"),
                },
                "instruction": "הצג/י ללקוח את פרטי ההזמנה ושאל/י במפורש: 'לאשר את הביטול?'. "
                               "בצע/י שוב עם confirm=true רק אחרי שהלקוח אישר במילים ברורות. "
                               "חשוב לציין: הביטול סופי, והזיכוי יתבצע לאמצעי התשלום המקורי."}

    # --- execute ---
    _persist_status(order_id, "בוטל")
    audit.record(session_id, "cancel_order", order_id, "executed",
                 before=status, after="בוטל",
                 detail=f"items={order.get('items')}, total={order.get('total')}")
    return {"ok": True, "stage": "executed",
            "message": f"ההזמנה {order_id} בוטלה בהצלחה. "
                       f"זיכוי על סך {order.get('total')} ש\"ח יתבצע לאמצעי התשלום המקורי "
                       f"בהתאם ללוחות הזמנים של חברת האשראי."}
