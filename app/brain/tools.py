"""Agent tool definitions (Claude function-calling) + dispatch to the store client."""
from typing import Dict, Any
from app.woocommerce.client import store

# escalate_to_human is handled by the agent loop, not the store — see agent.py.
TOOLS = [
    {
        "name": "search_products",
        "description": "חיפוש מוצרים בקטלוג לפי מילות מפתח (שם/סוג/קטגוריה). אפשר לסנן לפי מבצע ומחיר מקסימלי.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "מילות חיפוש בעברית, למשל 'ג'ינס סלים' או 'חולצה מכופתרת'"},
                "on_sale": {"type": "boolean", "description": "להחזיר רק מוצרים במבצע"},
                "max_price": {"type": "number", "description": "מחיר מקסימלי בשקלים"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_product_details",
        "description": "פרטים מלאים על מוצר לפי מק\"ט (sku): תיאור, מחיר, צבעים, מידות, זמינות.",
        "input_schema": {
            "type": "object",
            "properties": {"sku": {"type": "string"}},
            "required": ["sku"],
        },
    },
    {
        "name": "check_stock",
        "description": "בדיקת זמינות/מלאי חי של מוצר, כולל צבעים ומידות זמינים. אפשר לבדוק צירוף צבע+מידה ספציפי.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sku": {"type": "string"},
                "color": {"type": "string"},
                "size": {"type": "string"},
            },
            "required": ["sku"],
        },
    },
    {
        "name": "check_order_status",
        "description": "בדיקת סטטוס הזמנה לפי מספר הזמנה או מספר טלפון. מחזיר סטטוס והאם צריך העברה לנציג.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "phone": {"type": "string"},
            },
        },
    },
    {
        "name": "list_branches",
        "description": "רשימת סניפים ושעות פתיחה. אפשר לסנן לפי עיר.",
        "input_schema": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
        },
    },
    {
        "name": "get_faq",
        "description": ("בסיס הידע המלא של החנות: משלוחים, החזרות והחלפות, ביטול, טבלאות מידות, אבטחה, "
                        "תהליך הזמנה, אמצעי תשלום, אודות. חובה לחפש כאן לפני שמכריזים שאין מידע על שאלה "
                        "כללית. אפשר לציין נושא."),
        "input_schema": {
            "type": "object",
            "properties": {"topic": {"type": "string", "description": "למשל 'משלוח', 'החזרות', 'מידות', 'תשלום'"}},
        },
    },
    {
        "name": "cancel_order",
        "description": ("ביטול הזמנה — פעולה אמיתית במערכת! פרוטוקול חובה: "
                        "(1) דרוש/י מהלקוח מספר הזמנה + הטלפון שאיתו בוצעה (אימות זהות). "
                        "(2) קריאה ראשונה בלי confirm — המערכת תאמת ותחזיר סיכום לאישור. "
                        "(3) הצג/י את הסיכום ושאל/י 'לאשר את הביטול?'. "
                        "(4) רק אחרי 'כן' מפורש מהלקוח — קריאה שנייה עם confirm=true. "
                        "אם המערכת מסרבת (זהות/סטטוס) — פעל/י לפי ההנחיה שתוחזר."),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "מספר ההזמנה לביטול"},
                "phone": {"type": "string", "description": "הטלפון שאיתו בוצעה ההזמנה (חובה לאימות)"},
                "confirm": {"type": "boolean", "description": "true רק אחרי שהלקוח אישר במפורש את הביטול"},
            },
            "required": ["order_id", "phone"],
        },
    },
    {
        "name": "create_service_ticket",
        "description": ("פתיחת פנייה לבירור — כשאין תשובה מאומתת לשאלה ספציפית על העסק (מדיניות שלא מתועדת, "
                        "מקרה חריג). הפנייה נרשמת, נציג יבדוק ויחזור ללקוח עם תשובה מדויקת. זו הדרך המועדפת "
                        "לטפל בפערי ידע — במקום להעביר את השיחה לנציג ובמקום לנחש."),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "נושא הפנייה בקצרה, למשל 'אמצעי תשלום'"},
                "question": {"type": "string", "description": "השאלה המדויקת של הלקוח"},
                "customer_name": {"type": "string", "description": "שם הלקוח אם ידוע"},
                "phone": {"type": "string", "description": "טלפון לחזרה אם נמסר"},
            },
            "required": ["topic", "question"],
        },
    },
    {
        "name": "escalate_to_human",
        "description": ("העברת השיחה החיה לנציג אנושי — מוצא אחרון. רק כאשר: הלקוח ביקש נציג במפורש, "
                        "הלקוח כועס/מתוסכל, הזמנה חורגת/אבודה, או שני ניסיונות מענה נכשלו. "
                        "לפערי ידע רגילים העדף create_service_ticket."),
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string", "description": "סיבת ההעברה בקצרה"}},
            "required": ["reason"],
        },
    },
]


def dispatch(name: str, args: Dict[str, Any], session_id: str = "unknown") -> Dict[str, Any]:
    """Execute a store-backed tool. Returns a JSON-serializable result."""
    if name == "cancel_order":
        from app.actions import orders
        return orders.cancel_order(session_id, args.get("order_id", ""),
                                   args.get("phone", ""), confirm=bool(args.get("confirm")))
    if name == "create_service_ticket":
        from app.handoff import tickets
        t = tickets.create(session_id, args.get("topic", ""), args.get("question", ""),
                           customer_name=args.get("customer_name"), phone=args.get("phone"))
        return {"created": True, "ticket_id": t["id"],
                "message": "הפנייה נרשמה. נציג יבדוק ויחזור ללקוח עם תשובה מדויקת."}
    if name == "search_products":
        return {"results": store.search_products(
            args["query"], on_sale=args.get("on_sale"), max_price=args.get("max_price"))}
    if name == "get_product_details":
        p = store.get_product(args["sku"])
        return p or {"found": False}
    if name == "check_stock":
        return store.check_stock(args["sku"], color=args.get("color"), size=args.get("size"))
    if name == "check_order_status":
        return store.check_order_status(order_id=args.get("order_id"), phone=args.get("phone"))
    if name == "list_branches":
        return {"branches": store.list_branches(city=args.get("city"))}
    if name == "get_faq":
        return {"content": store.get_policies(args.get("topic"))[:2500]}
    return {"error": f"unknown tool {name}"}
