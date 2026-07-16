"""FastAPI app: health, a /chat endpoint for testing, and a /webhook stub for the
WhatsApp gateway (Evolution API) — wired in a later milestone."""
import os
import tempfile
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from typing import Optional

from app.config import settings
from app.brain.agent import handle_message, close_conversation, record_correction
from app.woocommerce.client import store as store_client
from app.whatsapp.webhook import parse_incoming
from app.learning.feedback import report as feedback_report

app = FastAPI(title="WhatsApp Customer Service Bot", version="0.1.0")

# The chat UI can be hosted elsewhere (e.g. Cloudflare Pages — deploy/cloudflare/site/)
# and call this API cross-origin. Lock down with ALLOWED_ORIGINS=https://your.pages.dev
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _warm_up():
    """Pre-load the heavy lazy singletons in the background (embedding model + style/lesson
    vector matrices) so the FIRST customer message isn't blocked for seconds by torch init."""
    import threading

    def warm():
        try:
            from app.learning.embeddings import get_embedder
            if get_embedder().available:
                from app.brain import style
                from app.learning import lessons
                style.retrieve("חולצה")            # loads the bank + embeds it
                lessons.retrieve("faq", "משלוח")   # loads + embeds the lessons file
                print("[startup] embeddings warmed")
        except Exception as e:
            print(f"[startup] warmup skipped: {type(e).__name__}: {str(e)[:120]}")

    threading.Thread(target=warm, daemon=True).start()


class ChatIn(BaseModel):
    session_id: str = "demo"
    message: str


@app.get("/", response_class=HTMLResponse)
def chat_ui():
    """WhatsApp-style demo chat UI."""
    return (Path(__file__).parent / "static" / "chat.html").read_text(encoding="utf-8")


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_ui():
    """Business dashboard: KPIs, intents, tickets (resolve→teach), lessons, feed."""
    return (Path(__file__).parent / "static" / "dashboard.html").read_text(encoding="utf-8")


@app.get("/api/dashboard")
def dashboard_data():
    from app.analytics.dashboard import snapshot
    return snapshot()


_report_cache: dict = {}   # {days: (built_at_epoch, report)} — avoid re-billing the LLM on refresh


def _weekly(days: int, refresh: bool = False):
    import time as _t
    from app.analytics import weekly_report as WR
    hit = _report_cache.get(days)
    if hit and not refresh and (_t.time() - hit[0] < 600):   # 10-min cache
        return hit[1]
    report = WR.build(days=days)
    _report_cache[days] = (_t.time(), report)
    return report


@app.get("/report/weekly")
def weekly_json(days: int = 7, refresh: bool = False):
    """Weekly report as JSON (metrics + AI analysis). Cached 10 min."""
    return _weekly(days, refresh)


@app.get("/report", response_class=HTMLResponse)
def weekly_html(days: int = 7, refresh: bool = False):
    """Rendered weekly report page (closures, upset customers, profit/loss, what to improve)."""
    from app.analytics import weekly_report as WR
    return WR.render_html(_weekly(days, refresh))


@app.get("/health")
def health():
    return {"status": "ok", "claude": settings.has_claude, "store": store_client.stats()}


_IMG_ALLOWED = {"www.maniajeans.co.il", "maniajeans.co.il"}
_IMG_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.maniajeans.co.il/"}
_IMG_CACHE_DIR = Path(__file__).parent.parent / "data" / "cache" / "img"
_IMG_TIMEOUT = 6                 # one bad URL may cost at most ~6s, once — then it's neg-cached
_img_neg: dict = {}              # url -> retry-after epoch; dead URLs answer instantly
_IMG_NEG_TTL = 600
# neutral placeholder returned (200) when a photo truly can't be fetched — so the <img> never
# errors and the product card never disappears from the chat.
_IMG_PLACEHOLDER = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="134" height="158">'
    b'<rect width="100%" height="100%" fill="#eceff1"/>'
    b'<text x="50%" y="52%" fill="#b0bec5" font-family="Arial" font-size="12" '
    b'text-anchor="middle">\xd7\x9e\xd7\x90\xd7\xa0\xd7\x99\xd7\x94 \xd7\x92\xd7\xb3\xd7\x99\xd7\xa0\xd7\xa1</text></svg>'
)


def _img_allowed(u: str) -> bool:
    from urllib.parse import urlparse
    return bool(u) and (urlparse(u).hostname or "") in _IMG_ALLOWED


def _img_cache_path(u: str) -> Path:
    import hashlib
    return _IMG_CACHE_DIR / hashlib.md5(u.encode()).hexdigest()


def _img_cache_get(u: str):
    """Disk-cached (bytes, ctype) — instant, survives restarts."""
    p = _img_cache_path(u)
    try:
        if p.is_file():
            return p.read_bytes(), (p.with_suffix(".ct").read_text().strip() or "image/jpeg")
    except Exception:
        pass
    return None


def _img_cache_put(u: str, body: bytes, ctype: str) -> None:
    try:
        _IMG_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p = _img_cache_path(u)
        p.write_bytes(body)
        p.with_suffix(".ct").write_text(ctype)
    except Exception:
        pass


def _img_neg_active(u: str) -> bool:
    import time as _t
    return _img_neg.get(u, 0) > _t.time()


def _img_neg_mark(u: str) -> None:
    import time as _t
    if len(_img_neg) > 2000:
        _img_neg.clear()
    _img_neg[u] = _t.time() + _IMG_NEG_TTL


def _img_ok(r) -> bool:
    return r.status_code == 200 and r.headers.get("content-type", "").startswith("image")


def _fetch_img(u: str):
    """Sync fetch (WhatsApp product cards run in a background thread). Disk-cache first;
    a failure is negative-cached so a dead URL never blocks repeatedly."""
    import httpx
    if not _img_allowed(u):
        return None
    hit = _img_cache_get(u)
    if hit:
        return hit
    if _img_neg_active(u):
        return None
    try:
        r = httpx.get(u, timeout=_IMG_TIMEOUT, follow_redirects=True, headers=_IMG_HEADERS)
        if _img_ok(r):
            out = (r.content, r.headers.get("content-type", "image/jpeg"))
            _img_cache_put(u, *out)
            return out
    except Exception:
        pass
    _img_neg_mark(u)
    return None


_img_aclient = None
_img_sem = None


async def _afetch_img(u: str):
    """Async fetch for the /img endpoint — never occupies a worker thread, capped at 8
    concurrent upstream requests, so a burst of product cards can't stall the server."""
    import asyncio
    import httpx
    global _img_aclient, _img_sem
    if not _img_allowed(u):
        return None
    hit = _img_cache_get(u)
    if hit:
        return hit
    if _img_neg_active(u):
        return None
    if _img_aclient is None:
        _img_aclient = httpx.AsyncClient(timeout=_IMG_TIMEOUT, follow_redirects=True,
                                         headers=_IMG_HEADERS)
        _img_sem = asyncio.Semaphore(8)
    try:
        async with _img_sem:
            r = await _img_aclient.get(u)
        if _img_ok(r):
            out = (r.content, r.headers.get("content-type", "image/jpeg"))
            _img_cache_put(u, *out)
            return out
    except Exception:
        pass
    _img_neg_mark(u)
    return None


@app.get("/img")
async def img_proxy(u: str, fb: str = ""):
    """Proxy + cache catalog product photos so the demo UI shows them instantly and reliably:
    adds a browser Referer (defeats hotlink protection), avoids mixed-content, and NEVER hands the
    browser an error. Tries `u`, then the fallback `fb` (e.g. the product's default image), then a
    placeholder — always HTTP 200, so a card can't vanish. SSRF-guarded to the store's host.
    Async + disk cache + negative cache: slow/dead URLs can't pile up and stall the server."""
    from fastapi.responses import Response
    got = await _afetch_img(u) or (await _afetch_img(fb) if fb else None)
    if got:
        body, ctype = got
        return Response(content=body, media_type=ctype,
                        headers={"Cache-Control": "public, max-age=86400"})
    return Response(content=_IMG_PLACEHOLDER, media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


@app.post("/chat")
def chat(body: ChatIn):
    """Test endpoint — talk to the bot without WhatsApp."""
    return handle_message(body.session_id, body.message)


class CloseIn(BaseModel):
    session_id: str = "demo"
    rating: Optional[str] = None   # 'up' | 'down' | None (auto-assess)


class CorrectIn(BaseModel):
    session_id: str
    correction: str


@app.post("/close")
def close(body: CloseIn):
    """End a conversation: assess satisfaction and auto-learn a lesson if warranted."""
    return close_conversation(body.session_id, explicit=body.rating)


@app.post("/correct")
def correct(body: CorrectIn):
    """A human agent supplies the correct answer -> distilled into a learned lesson."""
    return record_correction(body.session_id, body.correction)


@app.get("/learning/report")
def learning_report():
    """Satisfaction signals aggregated per intent, for review."""
    return feedback_report()


class ResolveIn(BaseModel):
    answer: str


@app.get("/tickets")
def tickets_list(status: Optional[str] = None):
    """Service tickets (knowledge gaps) — the business's follow-up queue."""
    from app.handoff import tickets
    return {"tickets": tickets.list_tickets(status=status)}


@app.post("/tickets/{ticket_id}/resolve")
def tickets_resolve(ticket_id: str, body: ResolveIn):
    """Answer a ticket AND teach the bot: the answer is distilled into a lesson so the
    next customer asking the same thing gets an instant, correct reply."""
    from app.handoff import tickets
    t = tickets.resolve(ticket_id, body.answer)
    if not t:
        return {"resolved": False, "error": "ticket not found"}
    lesson = record_correction(t["session_id"], body.answer)
    return {"resolved": True, "ticket": t, "lesson": lesson.get("lesson")}


async def _save_upload(file: UploadFile, default_ext: str) -> str:
    suffix = os.path.splitext(file.filename or "")[1] or default_ext
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        return tmp.name


@app.post("/voice")
async def voice(session_id: str = Form("demo"), file: UploadFile = File(...)):
    """Voice note -> transcribe (STT) -> normal pipeline. Always returns JSON."""
    from app.brain.agent import handle_voice
    path = await _save_upload(file, ".ogg")
    try:
        return handle_voice(session_id, path)
    except Exception as e:
        return {"reply": "לא הצלחנו לעבד את ההקלטה. אפשר לנסות שוב?",
                "handoff": False, "error": str(e)[:120]}
    finally:
        os.unlink(path)


@app.post("/image")
async def image(session_id: str = Form("demo"), caption: str = Form(""),
                file: UploadFile = File(...)):
    """Photo -> Claude vision (damage verification) -> context-aware reply. Always returns JSON."""
    from app.brain.agent import handle_image
    path = await _save_upload(file, ".png")
    try:
        return handle_image(session_id, path, caption=caption)
    except Exception as e:
        return {"reply": "לא הצלחנו לעבד את התמונה. אפשר לשלוח שוב, רצוי כ-JPG?",
                "handoff": False, "error": str(e)[:120]}
    finally:
        os.unlink(path)


def _process_whatsapp(msg: dict):
    """Full WhatsApp pipeline for one parsed inbound message (runs in the background so
    the webhook acks fast and Evolution never retries): route text/image/voice through
    the brain, then answer on WhatsApp — text reply + up to 3 product photos as native
    media. Handoff notification happens inside escalation (covers web + WhatsApp)."""
    from app.whatsapp import gateway
    from app.whatsapp.media import save_incoming
    from app.memory.conversations import store as conv_store

    to = msg["session_id"]                      # in WhatsApp the session id == the phone number
    session = conv_store.get(to)
    if msg.get("push_name") and not session.facts.get("name"):
        session.set_fact("name", msg["push_name"])
    gateway.send_typing(to)

    try:
        if msg["kind"] == "text":
            result = handle_message(to, msg["text"])
        else:
            path = save_incoming(msg)
            if not path:
                gateway.send_message(to, "קיבלנו את הקובץ אבל לא הצלחנו לפתוח אותו. "
                                         "אפשר לשלוח שוב, או פשוט לכתוב לנו?")
                return
            try:
                if msg["kind"] == "image":
                    from app.brain.agent import handle_image
                    result = handle_image(to, path, caption=msg.get("caption", ""))
                else:
                    from app.brain.agent import handle_voice
                    result = handle_voice(to, path)
            finally:
                os.unlink(path)
    except Exception as e:
        print(f"[webhook] pipeline error for {to}: {type(e).__name__}: {str(e)[:150]}")
        gateway.send_message(to, "משהו השתבש אצלנו לרגע. אפשר לנסות שוב עוד דקה?")
        return

    if result.get("reply"):
        gateway.send_message(to, result["reply"])
    if result.get("products"):
        gateway.send_product_cards(to, result["products"], limit=3,
                                   fetch_image=lambda u: _fetch_img(u) if u else None)


async def _webhook_impl(payload: dict, background: BackgroundTasks):
    msg = parse_incoming(payload)
    if not msg:
        return {"ignored": True}
    background.add_task(_process_whatsapp, msg)   # ack now; brain + reply run after response
    return {"ok": True}


@app.post("/webhook")
async def webhook(payload: dict, background: BackgroundTasks):
    """Inbound WhatsApp events from Evolution API. Only for unsecured setups —
    when WA_WEBHOOK_SECRET is set, Evolution is registered on /webhook/{token}."""
    if settings.WA_WEBHOOK_SECRET:
        return {"ignored": True, "reason": "secret required"}
    return await _webhook_impl(payload, background)


@app.post("/webhook/{token}")
async def webhook_secured(token: str, payload: dict, background: BackgroundTasks):
    """Same webhook, with the shared secret in the path (registered by app.whatsapp.setup)."""
    if not settings.WA_WEBHOOK_SECRET or token != settings.WA_WEBHOOK_SECRET:
        return {"ignored": True, "reason": "bad token"}
    return await _webhook_impl(payload, background)
