"""Computer vision — analyze a customer's photo with Claude (multimodal).

GROUNDED in what the store actually sells. Two jobs, in order:
  1. Is the photographed item even a TYPE of product this store sells? (men's fashion)
     If not (an appliance, a random object, a wrong photo) -> is_our_product=no, and the bot
     tells the customer this isn't one of our products — it does NOT opine on a foreign item
     as if it were ours.
  2. Only if it IS our kind of product -> verify a damage/defect claim before refund/replacement.
"""
import json
import base64
import re
from typing import Dict, Any

from app.config import settings

import io


def _prepare_image(path: str):
    """Open (any decodable format, incl. HEIC if pillow-heif is present), convert to RGB,
    downscale so the long edge ≤ 1568px, and re-encode to JPEG base64. This normalizes size
    and format so Claude never rejects the upload. Returns (b64, "image/jpeg") or None."""
    try:
        try:
            import pillow_heif  # optional: iPhone HEIC support
            pillow_heif.register_heif_opener()
        except Exception:
            pass
        from PIL import Image
        img = Image.open(path)
        img = img.convert("RGB")
        if max(img.size) > 1568:
            img.thumbnail((1568, 1568))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return base64.standard_b64encode(buf.getvalue()).decode("utf-8"), "image/jpeg"
    except Exception as e:
        print(f"[vision] could not read image: {type(e).__name__}: {str(e)[:100]}")
        return None

# What the store actually sells — the vision model must ground against this.
_STORE_SELLS = (
    "אופנת גברים בלבד: ביגוד (ג'ינסים, מכנסיים, מכנסיים קצרים, חולצות טי, מכופתרות, פוטרים, "
    "סווטשירטים, מעילים וג'קטים, חליפות, סטים, בגדי ים, גופיות, תחתונים/בוקסרים), "
    "הנעלה (נעליים, סניקרס), ואביזרים (חגורות, עניבות). לא מוצרי חשמל, לא כלים, לא מזון, לא חפצים אחרים."
)

_SYSTEM = (
    "אתה מנתח/ת תמונות עבור מוקד שירות של חנות אופנה לגברים בשם 'מאניה ג'ינס'.\n"
    f"החנות מוכרת {_STORE_SELLS}\n"
    "יש לך שתי משימות לפי הסדר:\n"
    "1. לקבוע אם הפריט בתמונה הוא **סוג מוצר שהחנות בכלל מוכרת**. אם התמונה מציגה משהו אחר "
    "(מכשיר, כלי, חפץ שאינו פריט אופנת גברים) — is_our_product=no, ואל תתייחס/י אליו כמו למוצר שלנו.\n"
    "2. רק אם זה כן סוג מוצר שלנו — לאמת נזק/פגם לצורך החזר. אשר/י נזק רק אם הוא נראה בבירור. "
    "אם התמונה לא ברורה — unclear.\n"
    "היה/י אובייקטיבי/ת ומדויק/ת. אל תמציא/י פרטים שלא בתמונה."
)


def analyze_damage(image_path: str, caption: str = "") -> Dict[str, Any]:
    if not settings.has_claude:
        return {"ok": False, "summary": "ניתוח תמונה לא זמין (אין חיבור ל-AI).",
                "is_our_product": "unclear", "damage_visible": "unclear"}
    prepared = _prepare_image(image_path)
    if prepared is None:
        return {"ok": False, "is_our_product": "unclear", "damage_visible": "unclear",
                "summary": "לא הצלחנו לקרוא את קובץ התמונה."}
    data, media_type = prepared

    import anthropic
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    prompt = (
        f"נתח/י את התמונה. תיאור הלקוח: \"{caption or 'לא צוין'}\".\n"
        "החזר/י JSON בלבד: {"
        "\"item\": \"מה רואים בתמונה\", "
        "\"is_our_product\": \"yes|no|unclear\", "
        "\"damage_visible\": \"yes|no|unclear\", "
        "\"damage_type\": \"תיאור קצר של הנזק אם יש\", "
        "\"description\": \"תיאור אובייקטיבי קצר\", "
        "\"recommendation\": \"approve_refund|request_better_photo|no_damage|not_our_product|escalate\"}"
    )
    try:
        resp = client.messages.create(
            model=settings.CLAUDE_MODEL, max_tokens=500, system=_SYSTEM,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
                {"type": "text", "text": prompt},
            ]}])
        from app.analytics import costs
        costs.record_usage(settings.CLAUDE_MODEL, getattr(resp, "usage", None))
        txt = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        m = re.search(r"\{.*\}", txt, re.S)
        d = json.loads(m.group(0)) if m else {}
    except Exception as e:
        print(f"[vision] Claude call failed: {type(e).__name__}: {str(e)[:100]}")
        return {"ok": False, "is_our_product": "unclear", "damage_visible": "unclear",
                "summary": "לא הצלחנו לנתח את התמונה כרגע."}
    d["ok"] = True
    d.setdefault("is_our_product", "unclear")
    dv = d.get("damage_visible", "unclear")
    item = d.get("item", "הפריט")

    if d["is_our_product"] == "no":
        d["recommendation"] = "not_our_product"
        d["summary"] = (f"הפריט בתמונה ({item}) אינו מסוג המוצרים שהחנות מוכרת "
                        f"(מאניה ג'ינס = אופנת גברים). ייתכן שנשלחה תמונה שגויה.")
    elif dv == "yes":
        d["summary"] = f"נראה נזק בתמונה — {item}: {d.get('damage_type', '')}"
    elif dv == "no":
        d["summary"] = f"לא נראה נזק בתמונה ({item} נראה תקין)."
    else:
        d["summary"] = "התמונה לא ברורה מספיק לקביעת נזק."
    return d


def available() -> bool:
    return settings.has_claude
