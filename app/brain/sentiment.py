"""Lightweight sentiment / frustration detection (Hebrew), keyword+signal based.

Runs every turn with zero API cost/latency. Returns a level:
  "calm" | "annoyed" | "angry"
The agent uses it to (a) shift tone (more apologetic, formal, no upsell) and
(b) force a human handoff when the customer is angry — with a summary so they
don't repeat themselves.
"""
from typing import Dict, Any

# strong: legal/threat/shame → treat as angry
_ANGRY = [
    "בושה", "תביעה", "אתבע", "עורך דין", "עורך-דין", "רשות", "תלונה", "נמאס",
    "גועל", "שערורייה", "גנבים", "רמאים", "נוכלים", "מזלזלים", "לא ייאמן",
    "בזבזתי", "הכי גרוע", "עוקצים", "מחפיר",
]
# moderate: waiting/repetition/annoyance → annoyed
_ANNOYED = [
    "מחכה כבר", "שבועיים", "שלושה שבועות", "חודש", "פעם שלישית", "פעם רביעית",
    "שוב פעם", "כמה זמן עוד", "מתי כבר", "לא מקבל תשובה", "אף אחד לא", "עד מתי",
    "מאוכזב", "מתוסכל", "לא מרוצה", "עצבים", "די כבר",
]


def analyze(text: str) -> Dict[str, Any]:
    t = text or ""
    low = t.replace("״", '"')
    excls = low.count("!") + low.count("?!")
    # NOTE: no all-caps "shouting" heuristic — Hebrew has no caps, and English caps in a
    # Hebrew chat are almost always product/brand codes (DORI, GAL...), not anger.

    angry = any(w in low for w in _ANGRY)
    annoyed = any(w in low for w in _ANNOYED)

    if angry or excls >= 4:
        level = "angry"
    elif annoyed or excls >= 2:
        level = "annoyed"
    else:
        level = "calm"

    return {"level": level, "exclamations": excls}


# Tone guidance injected into the system prompt when the customer is not calm.
_TONE = {
    "annoyed": (
        "## מצב רוח: הלקוח קצת מתוסכל\n"
        "- פתח/י בהכרה קצרה בתסכול (\"אני מבינ/ה שזה מעצבן\"). בלי התנצלות מוגזמת.\n"
        "- ענייני/ת וממוקד/ת — פתרון קודם, בלי הצעות מכירה/upsell עכשיו.\n"
    ),
    "angry": (
        "## מצב רוח: הלקוח כועס מאוד\n"
        "- שנה/י טון למתנצל, רשמי ואמפתי. התנצל/י בכנות על חוסר הנוחות.\n"
        "- אל תמכור/י כלום. אל תתגונן/י ואל תסביר/י נהלים ארוכים.\n"
        "- העבר/י מיד לנציג אנושי (escalate_to_human) עם סיבה שמסכמת את הבעיה, "
        "כדי שהלקוח לא יצטרך לחזור על עצמו.\n"
    ),
}


def tone_note(level: str) -> str:
    return _TONE.get(level, "")
