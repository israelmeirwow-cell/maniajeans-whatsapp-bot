"""The simulated customer — a cheap Haiku call that plays a persona and generates the next
customer message given the conversation so far. Keeps messages short so the customer side
stays a fraction of an agora per turn."""
import random
from typing import List, Tuple

from app.config import settings
from app.analytics import costs

GREETING = ("היי, כאן שירות הלקוחות של מאניה ג'ינס. "
            "אפשר לשאול על מוצרים, הזמנות, משלוחים וסניפים. איך אפשר לעזור?")

# Bitext-derived language-variation styles (B/I/N/P/Q/K/E/Z) in Hebrew
STYLE_MODES = [
    "כתוב במשפט רגיל ופשוט",
    "נסח כשאלה",
    "נסח דרך שלילה (למשל 'זה לא מה שביקשתי')",
    "כתוב מנומס ורשמי ('אשמח אם תוכלו', 'תודה מראש')",
    "כתוב בסלנג ישראלי כבד בלי פיסוק ('וואלה', 'אחי', 'בקיצור')",
    "כתוב במילות מפתח בלבד בלי משפט שלם ('גינס שחור 32 מלאי?')",
    "כתוב בקיצורים ובכתיבה מהירה ('אפש', 'בבקש', 'תודהה')",
    "כתוב עם שגיאות כתיב והקלדה אמיתיות",
]


def next_message(persona: dict, history: List[Tuple[str, str]], client) -> str:
    """history is a list of (role, text) with role in {'customer','bot'}. From the customer
    model's POV the store's turns are 'user' and its own turns are 'assistant'."""
    msgs = []
    for role, text in history:
        msgs.append({"role": "user" if role == "bot" else "assistant", "content": text})
    if not msgs or msgs[0]["role"] != "user":
        msgs.insert(0, {"role": "user", "content": GREETING})

    style = random.choice(STYLE_MODES)   # Bitext-style register variation per message
    resp = client.messages.create(
        model=settings.CLAUDE_CLASSIFIER_MODEL,   # Haiku — cheap
        max_tokens=80,
        system=persona["system"] + f"\nסגנון ההודעה הזו: {style}.",
        messages=msgs,
    )
    costs.record_usage(settings.CLAUDE_CLASSIFIER_MODEL, getattr(resp, "usage", None))
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    # strip accidental quotes/prefixes
    return text.strip().strip('"').strip("'")
