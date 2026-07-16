"""Materialize inbound WhatsApp media (images / voice notes) to a temp file.

Source order: the base64 Evolution inlined in the webhook (webhookBase64=true),
else a getBase64FromMediaMessage fetch by message key. Returns a local file path
the brain's handle_image / handle_voice can consume, or None. Caller deletes.
"""
import base64
import tempfile
from typing import Any, Dict, Optional

from app.whatsapp import gateway

_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
        "audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/mp4": ".m4a"}


def _ext_for(mimetype: str, default: str) -> str:
    return _EXT.get((mimetype or "").split(";")[0].strip(), default)


def save_incoming(msg: Dict[str, Any]) -> Optional[str]:
    """`msg` is the parsed webhook dict (kind=image|audio). Returns a temp file path."""
    b64 = msg.get("base64") or ""
    mimetype = msg.get("mimetype") or ""
    if not b64:
        fetched = gateway.fetch_media_base64(msg.get("key") or {})
        if not fetched:
            return None
        b64, mimetype = fetched["base64"], fetched.get("mimetype") or mimetype
    if "," in b64[:80]:                       # strip a data:...;base64, prefix if present
        b64 = b64.split(",", 1)[1]
    try:
        blob = base64.b64decode(b64)
    except Exception:
        return None
    if not blob:
        return None
    default = ".jpg" if msg.get("kind") == "image" else ".ogg"
    with tempfile.NamedTemporaryFile(delete=False, suffix=_ext_for(mimetype, default)) as tmp:
        tmp.write(blob)
        return tmp.name
