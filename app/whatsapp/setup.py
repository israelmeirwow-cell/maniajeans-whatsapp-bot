"""One-command WhatsApp onboarding: python -m app.whatsapp.setup [--public-url URL]

Walks the whole Phase-1 flow against the Evolution API server:
  1. create the instance (skips if it already exists)
  2. fetch the pairing QR and save it as a PNG (opens it on macOS) — scan from
     the dedicated phone: WhatsApp > Settings > Linked Devices > Link a Device
  3. wait for the link (polls connectionState until 'open')
  4. register our webhook (MESSAGES_UPSERT, media inlined as base64)
  5. optional smoke test: send "בדיקה" to a number you type

Needs in .env: WA_GATEWAY_URL, WA_GATEWAY_API_KEY, WA_INSTANCE_NAME
(and WA_WEBHOOK_SECRET recommended). The bot server must be reachable from the
Evolution server at --public-url (default http://host.docker.internal:8123 for
a local Docker Evolution talking to a local bot).
"""
import argparse
import base64
import subprocess
import sys
import time
from pathlib import Path

from app.config import settings, DATA_DIR
from app.whatsapp import gateway


def _fail(msg: str):
    print(f"✗ {msg}")
    sys.exit(1)


def _save_qr() -> bool:
    res = gateway.connect_qr()
    body = res.get("response") or {}
    b64 = ""
    if isinstance(body, dict):
        b64 = (body.get("qrcode") or {}).get("base64") or body.get("base64") or ""
        code = (body.get("qrcode") or {}).get("pairingCode") or body.get("pairingCode")
        if code:
            print(f"  קוד צימוד (אפשר במקום QR): {code}")
    if not b64:
        return False
    if "," in b64[:40]:
        b64 = b64.split(",", 1)[1]
    out = DATA_DIR / "wa_qr.png"
    out.write_bytes(base64.b64decode(b64))
    print(f"  QR נשמר: {out}")
    if sys.platform == "darwin":
        subprocess.run(["open", str(out)], check=False)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--public-url", default="http://host.docker.internal:8123",
                    help="כתובת שבה שרת הבוט נגיש מ-Evolution (ברירת מחדל: דוקר מקומי)")
    ap.add_argument("--skip-qr", action="store_true", help="דלג על שלב הצימוד (כבר מחובר)")
    args = ap.parse_args()

    if not gateway.is_configured():
        _fail("חסר WA_GATEWAY_URL / WA_INSTANCE_NAME ב-.env (ראה .env.example)")

    print(f"→ Evolution: {settings.WA_GATEWAY_URL} | instance: {settings.WA_INSTANCE_NAME}")

    state = gateway.connection_state()
    print(f"→ מצב חיבור נוכחי: {state}")

    if state in ("error", "close") or state == "unconfigured":
        print("→ יוצר instance...")
        res = gateway.create_instance()
        if res.get("sent"):
            print("  נוצר.")
        elif res.get("status") in (401,):
            _fail("apikey שגוי (WA_GATEWAY_API_KEY חייב להיות ה-AUTHENTICATION_API_KEY של Evolution)")
        else:
            print(f"  כנראה קיים כבר (status {res.get('status')}) — ממשיך.")

    if not args.skip_qr and gateway.connection_state() != "open":
        print("→ מושך QR לצימוד. סרוק מהטלפון הייעודי: וואטסאפ > הגדרות > מכשירים מקושרים")
        if not _save_qr():
            print("  אין QR זמין כרגע — אולי כבר מחובר, בודק...")
        print("→ ממתין לחיבור (עד 3 דקות)...")
        deadline = time.time() + 180
        while time.time() < deadline:
            state = gateway.connection_state()
            if state == "open":
                break
            time.sleep(4)
        if gateway.connection_state() != "open":
            _fail("הטלפון לא צומד בזמן. הרץ שוב את הסקריפט לקבלת QR טרי.")
    print("✓ מחובר לוואטסאפ (state=open)")

    print(f"→ רושם webhook אל {args.public_url} ...")
    res = gateway.set_webhook(args.public_url)
    if not res.get("sent"):
        _fail(f"רישום ה-webhook נכשל: {res}")
    print("✓ webhook פעיל (MESSAGES_UPSERT, מדיה כ-base64)")

    to = input("→ בדיקת שליחה — מספר בפורמט 9725XXXXXXXX (Enter לדלג): ").strip()
    if to:
        r = gateway.send_message(to, "בדיקת מערכת — הבוט של מאניה ג'ינס מחובר ✓")
        print("✓ נשלח" if r.get("sent") else f"✗ שליחה נכשלה: {r}")

    print("\nהכל מוכן. ודא שהשרת רץ:  python3 -m uvicorn app.main:app --port 8123")


if __name__ == "__main__":
    main()
