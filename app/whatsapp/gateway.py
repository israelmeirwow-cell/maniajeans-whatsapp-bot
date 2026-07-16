"""WhatsApp gateway abstraction (Evolution API v2).

All outbound WhatsApp I/O goes through here so business logic never touches the
underlying transport. Phase 1 = Evolution API in Baileys mode; Phase 2 = the same
Evolution API pointed at the official WhatsApp Business Cloud API — same contract.

Every function is a graceful no-op ({"sent": False, "reason": "gateway not configured"})
until WA_GATEWAY_URL / WA_GATEWAY_API_KEY / WA_INSTANCE_NAME are set, so the demo web
UI keeps working with no Evolution instance running.
"""
import base64
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings

_TIMEOUT = 25


def is_configured() -> bool:
    return bool(settings.WA_GATEWAY_URL and settings.WA_INSTANCE_NAME)


def _url(path: str) -> str:
    return f"{settings.WA_GATEWAY_URL.rstrip('/')}{path}"


def _headers() -> Dict[str, str]:
    return {"apikey": settings.WA_GATEWAY_API_KEY, "Content-Type": "application/json"}


def _post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not is_configured():
        return {"sent": False, "reason": "gateway not configured"}
    try:
        r = httpx.post(_url(path), json=payload, headers=_headers(), timeout=_TIMEOUT)
        ok = r.status_code < 300
        body: Any = None
        try:
            body = r.json()
        except Exception:
            body = r.text[:300]
        if not ok:
            print(f"[gateway] POST {path} -> {r.status_code}: {str(body)[:200]}")
        return {"sent": ok, "status": r.status_code, "response": body}
    except Exception as e:
        print(f"[gateway] POST {path} failed: {type(e).__name__}: {str(e)[:150]}")
        return {"sent": False, "error": str(e)[:200]}


def _get(path: str) -> Dict[str, Any]:
    if not is_configured():
        return {"ok": False, "reason": "gateway not configured"}
    try:
        r = httpx.get(_url(path), headers=_headers(), timeout=_TIMEOUT)
        try:
            return {"ok": r.status_code < 300, "status": r.status_code, "response": r.json()}
        except Exception:
            return {"ok": r.status_code < 300, "status": r.status_code, "response": r.text[:300]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# ---------------- sending ----------------

def send_message(to: str, text: str) -> Dict[str, Any]:
    """Send a WhatsApp text message to `to` (phone digits or jid)."""
    if not (to and text):
        return {"sent": False, "reason": "missing to/text"}
    return _post(f"/message/sendText/{settings.WA_INSTANCE_NAME}",
                 {"number": to.split("@")[0], "text": text})


def send_typing(to: str, ms: int = 2500) -> Dict[str, Any]:
    """Show 'typing…' while the brain works — feels human. Best-effort."""
    return _post(f"/chat/sendPresence/{settings.WA_INSTANCE_NAME}",
                 {"number": to.split("@")[0], "presence": "composing", "delay": ms})


def send_image(to: str, media: str, caption: str = "",
               mimetype: str = "image/jpeg", filename: str = "product.jpg") -> Dict[str, Any]:
    """Send an image. `media` is a public URL or a raw base64 string (no data: prefix)."""
    if not (to and media):
        return {"sent": False, "reason": "missing to/media"}
    return _post(f"/message/sendMedia/{settings.WA_INSTANCE_NAME}",
                 {"number": to.split("@")[0], "mediatype": "image", "mimetype": mimetype,
                  "media": media, "fileName": filename, "caption": caption})


def send_product_cards(to: str, products: List[Dict[str, Any]], limit: int = 3,
                       fetch_image=None) -> int:
    """Send catalog results as native WhatsApp images (photo + name/price/link caption).

    `fetch_image(url) -> (bytes, ctype) | None` lets the caller reuse the store-photo
    fetcher (browser Referer defeats hotlink protection); when it fails we fall back to
    sending the URL and letting Evolution fetch it. Returns how many cards were sent.
    """
    sent = 0
    for p in (products or [])[:limit]:
        img = p.get("image") or p.get("image_default")
        if not img:
            continue
        price = f'{p.get("price")}₪'
        if p.get("on_sale") and p.get("price_was"):
            price = f'{p.get("price")}₪ במקום {p.get("price_was")}₪ (מבצע)'
        caption = "\n".join(x for x in [p.get("name"), price, p.get("url")] if x)
        media, mime = img, "image/jpeg"
        if fetch_image:
            got = fetch_image(img) or (fetch_image(p.get("image_default"))
                                       if p.get("image_default") and p.get("image_default") != img
                                       else None)
            if got:
                media, mime = base64.b64encode(got[0]).decode(), got[1] or "image/jpeg"
        if send_image(to, media, caption=caption, mimetype=mime).get("sent"):
            sent += 1
    return sent


def notify_agent(summary: str) -> Dict[str, Any]:
    """Real-time handoff alert to the human agent's WhatsApp (settings.HANDOFF_NOTIFY)."""
    if not settings.HANDOFF_NOTIFY:
        return {"sent": False, "reason": "HANDOFF_NOTIFY not set"}
    return send_message(settings.HANDOFF_NOTIFY, summary)


# ---------------- receiving media ----------------

def fetch_media_base64(key: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Download a received media message decoded to base64 (fallback when the webhook
    didn't inline it). `key` is the message key from the webhook ({id, remoteJid, ...})."""
    res = _post(f"/chat/getBase64FromMediaMessage/{settings.WA_INSTANCE_NAME}",
                {"message": {"key": key}, "convertToMp4": False})
    body = res.get("response") or {}
    if res.get("sent") and isinstance(body, dict) and body.get("base64"):
        return {"base64": body["base64"], "mimetype": body.get("mimetype", "")}
    return None


# ---------------- instance lifecycle (used by app.whatsapp.setup) ----------------

def create_instance() -> Dict[str, Any]:
    """Create the Baileys instance on the Evolution server (idempotent-ish: 403 if exists)."""
    return _post("/instance/create",
                 {"instanceName": settings.WA_INSTANCE_NAME,
                  "integration": "WHATSAPP-BAILEYS", "qrcode": True})


def connect_qr() -> Dict[str, Any]:
    """Fetch the pairing QR (base64 PNG) for linking a phone to the instance."""
    return _get(f"/instance/connect/{settings.WA_INSTANCE_NAME}")


def connection_state() -> str:
    """'open' = linked & ready, 'connecting', 'close', or 'unconfigured'/'error'."""
    if not is_configured():
        return "unconfigured"
    res = _get(f"/instance/connectionState/{settings.WA_INSTANCE_NAME}")
    body = res.get("response") or {}
    if isinstance(body, dict):
        inst = body.get("instance") or body
        return inst.get("state") or inst.get("connectionStatus") or "error"
    return "error"


def set_webhook(public_url: str) -> Dict[str, Any]:
    """Point Evolution's webhook at our /webhook endpoint (MESSAGES_UPSERT only,
    media inlined as base64). Sends both v2 field spellings — extras are ignored."""
    hook = public_url.rstrip("/")
    if settings.WA_WEBHOOK_SECRET:
        hook = f"{hook}/webhook/{settings.WA_WEBHOOK_SECRET}"
    else:
        hook = f"{hook}/webhook"
    conf = {"enabled": True, "url": hook, "byEvents": False, "base64": True,
            "webhookByEvents": False, "webhookBase64": True,
            "events": ["MESSAGES_UPSERT"]}
    return _post(f"/webhook/set/{settings.WA_INSTANCE_NAME}", {"webhook": conf, **conf})
