"""Local terminal chat — test the bot without WhatsApp.

Usage:
    python -m app.cli                 # interactive
    python -m app.cli "יש לכם ג'ינס סלים?"   # single message
"""
import sys
from app.config import settings
from app.brain.agent import handle_message

BANNER = f"""🤖 בוט שירות לקוחות — {settings.STORE_NAME} (דמו)
Claude: {'מחובר ✅' if settings.has_claude else 'לא מחובר (מצב fallback)'}
הקלד/י הודעה, או 'exit' ליציאה.\n"""


def _run(session_id: str, text: str) -> None:
    res = handle_message(session_id, text)
    tag = f"[{res['intent']}{' → נציג' if res['handoff'] else ''}]"
    print(f"\n🤖 {res['reply']}\n   {tag}\n")


def main() -> None:
    if len(sys.argv) > 1:
        _run("cli", " ".join(sys.argv[1:]))
        return
    print(BANNER)
    while True:
        try:
            text = input("👤 ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in ("exit", "quit", "יציאה"):
            break
        if text:
            _run("cli", text)


if __name__ == "__main__":
    main()
