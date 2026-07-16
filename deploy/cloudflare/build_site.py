"""Build the Cloudflare Pages package: deploy/cloudflare/site/

Takes the live UI (app/static/chat.html + dashboard.html) and produces a STATIC copy
whose API calls go to a configurable remote base URL (config.js) instead of the same
origin — so the site can live on Cloudflare Pages while the brain runs elsewhere
(home server via a tunnel, a VPS, or the Cloudflare Container in ../container/).

Re-run after any UI change:  python3 deploy/cloudflare/build_site.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent      # .../whatsapp_agent
SRC = ROOT / "app" / "static"
OUT = Path(__file__).resolve().parent / "site"

INJECT = ('<script src="config.js"></script>\n'
          '<script>const API_BASE = (window.API_BASE || "").replace(/\\/+$/, "");</script>\n')

CONFIG_JS = """// ▼▼▼ שנו רק את השורה הזו: הכתובת שבה שרת הבוט שלכם נגיש מהאינטרנט ▼▼▼
// דוגמאות:  "https://bot.yourdomain.com"   או   "https://xxxx.trycloudflare.com"
window.API_BASE = "https://CHANGE-ME.example.com";
"""

# exact source snippet -> replacement (a failed match must fail the build, not silently skip)
CHAT_EDITS = [
    ('let src = "/img?u=" + encodeURIComponent(p.image);',
     'let src = API_BASE + "/img?u=" + encodeURIComponent(p.image);'),
    ('const res = await fetch("/chat", {',
     'const res = await fetch(API_BASE + "/chat", {'),
    ('const res = await fetch(isImage ? "/image" : "/voice", { method: "POST", body: fd });',
     'const res = await fetch(API_BASE + (isImage ? "/image" : "/voice"), { method: "POST", body: fd });'),
]

DASH_EDITS = [
    ('const d = await (await fetch("/api/dashboard")).json();',
     'const d = await (await fetch(API_BASE + "/api/dashboard")).json();'),
    ('const res = await fetch(`/tickets/${id}/resolve`, {',
     'const res = await fetch(API_BASE + `/tickets/${id}/resolve`, {'),
    ('<a href="/report" target="_blank">',
     '<a id="report-link" href="#" target="_blank">'),
    ('<a href="/" target="_blank">פתח צ\'אט ↗</a>',
     '<a href="index.html" target="_blank">פתח צ\'אט ↗</a>'),
]

DASH_FIXUP = ('<script>document.getElementById("report-link").href = API_BASE + "/report";'
              '</script>\n</body>')


def _build(name: str, out_name: str, edits, tail_fix: str = "") -> None:
    html = (SRC / name).read_text(encoding="utf-8")
    for old, new in edits:
        if old not in html:
            sys.exit(f"✗ {name}: source changed, snippet not found:\n  {old}\n"
                     f"  → update deploy/cloudflare/build_site.py")
        html = html.replace(old, new)
    if "<head>" not in html:
        sys.exit(f"✗ {name}: no <head> tag")
    html = html.replace("<head>", "<head>\n" + INJECT, 1)
    if tail_fix:
        html = html.replace("</body>", tail_fix, 1)
    (OUT / out_name).write_text(html, encoding="utf-8")
    print(f"✓ {out_name}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = OUT / "config.js"
    if not cfg.exists():                       # never overwrite the user's edited API URL
        cfg.write_text(CONFIG_JS, encoding="utf-8")
        print("✓ config.js (ערכו את הכתובת בפנים!)")
    _build("chat.html", "index.html", CHAT_EDITS)
    _build("dashboard.html", "dashboard.html", DASH_EDITS, tail_fix=DASH_FIXUP)
    print(f"\nהחבילה מוכנה: {OUT}")
    print("העלאה: Cloudflare Dashboard → Workers & Pages → Create → Pages → Upload assets")


if __name__ == "__main__":
    main()
