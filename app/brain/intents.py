"""Hebrew intent classifier — routes each message into one of the 7 intents (CLAUDE.md §7)."""
from typing import List, Dict, Optional
from app.config import settings

# The 7 intents
ORDER_STATUS = "order_status"
PRODUCT_INFO = "product_info"
FAQ = "faq"
CANCEL_ORDER = "cancel_order"
REFUND_RETURN = "refund_return"
DISCOUNT = "discount"
UNRECOGNIZED = "unrecognized"

INTENTS = [ORDER_STATUS, PRODUCT_INFO, FAQ, CANCEL_ORDER, REFUND_RETURN, DISCOUNT, UNRECOGNIZED]

# Intents that always route straight to a human (bot does not self-serve).
# NOTE: CANCEL_ORDER was moved to bot self-service (guarded action tool) — the bot
# verifies identity, checks eligibility, and requires explicit confirmation.
HUMAN_INTENTS = {REFUND_RETURN, DISCOUNT}

_LABELS_HE = {
    ORDER_STATUS: "סטטוס/מעקב הזמנה",
    PRODUCT_INFO: "מידע על מוצר (מידות/צבעים/מלאי/מחיר)",
    FAQ: "שאלה נפוצה (משלוחים/שעות/סניפים/מדיניות כללית)",
    CANCEL_ORDER: "בקשה לבטל הזמנה",
    REFUND_RETURN: "זיכוי/החזר/החלפה/תלונה על מוצר פגום",
    DISCOUNT: "בקשת הנחה/מחיר מבצע/מיקוח",
    UNRECOGNIZED: "לא ברור / כללי / לא מתאים לאף קטגוריה",
}

_CLASSIFIER_SYSTEM = (
    "אתה מסווג/ת פניות של לקוחות לחנות אופנה. סווג/י את הודעת הלקוח לאחת מהקטגוריות הבאות בלבד, "
    "והחזר/י אך ורק את המזהה באנגלית (מילה אחת), בלי הסבר:\n"
    + "\n".join(f"- {k}: {v}" for k, v in _LABELS_HE.items())
    + "\n\nלקוחות בוואטסאפ כותבים מהר ומבולגן: שגיאות כתיב, בלי פיסוק, סלנג (וואלה, אחי, יא, בקיצור), "
      "ערבוב עברית-אנגלית, ולעיתים עברית באותיות לטיניות (תעתיק). פענח/י קודם את הכוונה האמיתית "
      "מאחורי הבלאגן, ורק אז סווג/י. סווג/י unrecognized רק אם באמת אי אפשר לפענח.\n"
      "אם מצורף הקשר משיחה קודמת — ההודעה היא המשך שלו: 'אוקיי עכשיו חולצה' אחרי שיחה על "
      "מוצרים היא product_info, 'ומה עם הכחול?' אחרי חיפוש מוצר היא product_info, "
      "'אז מה עם ההזמנה' אחרי בירור הזמנה היא order_status.\n"
      "בקשות סטיילינג — 'תרכיב לי לוק', 'מה ללבוש ל...', 'תלביש אותי' — הן product_info "
      "(הלקוח מבקש המלצות מוצרים).\n"
      "דוגמאות:\n"
      "'yesh lachem hulcot ktanot' → product_info\n"
      "'תרכיב לי לוק יומיומי' → product_info\n"
      "'אחי איפה ההזמנה שלי' → order_status\n"
      "'צריך אשראי בחזרה על משהו שקניתי' → refund_return\n"
      "'כמה זמן לוקח delivery' → faq"
)

# keyword fallback (used when no API key or on error)
_KEYWORDS = {
    CANCEL_ORDER: ["לבטל", "ביטול הזמנה", "בטל את ההזמנה", "לבטל הזמנה"],
    REFUND_RETURN: ["החזר", "זיכוי", "להחזיר", "החלפה", "פגום", "התקלקל", "קרוע", "מקולקל", "החזרה",
                    "אשראי בחזרה", "כסף בחזרה", "החזר כספי", "קרע", "נקרע", "שבור", "לא תקין"],
    DISCOUNT: ["הנחה", "מבצע", "זול יותר", "קופון", "הנחות", "מיקוח", "להוריד במחיר"],
    ORDER_STATUS: ["הזמנה שלי", "סטטוס", "מעקב", "איפה החבילה", "מתי יגיע", "המשלוח שלי",
                   "הזמנה מספר", "מספר הזמנה", "הזמנה", "החבילה", "משלוח שלי"],
    FAQ: ["שעות", "סניף", "סניפים", "משלוח", "כתובת", "טלפון", "מדיניות", "כמה זמן לוקח"],
    PRODUCT_INFO: ["מידה", "מידות", "צבע", "צבעים", "מלאי", "יש לכם", "כמה עולה", "מחיר", "במלאי",
                   "מוכרים", "דגם", "חולצה", "חולצות", "מכופתרת", "ג'ינס", "גינס", "מכנס", "מכנסיים",
                   "נעל", "נעליים", "מעיל", "ג'קט", "גקט", "פוטר", "טישרט", "טי שירט", "חגורה",
                   "בגד ים", "סריג", "בוקסר", "גופיה",
                   "לוק", "אאוטפיט", "סטיילינג", "מה ללבוש", "תלביש אותי"],
}


def _keyword_classify(text: str) -> str:
    t = text or ""
    for intent in (CANCEL_ORDER, REFUND_RETURN, DISCOUNT, ORDER_STATUS, FAQ, PRODUCT_INFO):
        if any(kw in t for kw in _KEYWORDS[intent]):
            return intent
    return UNRECOGNIZED


def classify(text: str, client=None, context: str = "") -> str:
    """Return one of the 7 intents. Uses Haiku if a client is given, else keyword fallback.
    `context` = the last few conversation turns, so short follow-ups ("אוקיי עכשיו חולצה")
    classify by what the chat is about instead of falling to unrecognized."""
    if client is None or not settings.has_claude:
        return _keyword_classify(text)
    content = (f"הקשר — סוף השיחה עד כה:\n{context}\n\nההודעה החדשה לסיווג:\n{text}"
               if context else text)
    try:
        resp = client.messages.create(
            model=settings.CLAUDE_CLASSIFIER_MODEL,
            max_tokens=12,
            system=_CLASSIFIER_SYSTEM,
            messages=[{"role": "user", "content": content}],
        )
        from app.analytics import costs
        costs.record_usage(settings.CLAUDE_CLASSIFIER_MODEL, getattr(resp, "usage", None))
        out = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip().lower()
        for intent in INTENTS:
            if intent in out:
                return intent
        return UNRECOGNIZED
    except Exception:
        return _keyword_classify(text)
