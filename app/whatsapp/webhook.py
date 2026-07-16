"""Parse inbound webhook payloads from the WhatsApp gateway (Evolution API v2).

Normalizes a MESSAGES_UPSERT event into one of:
  {"kind": "text",  "session_id", "from", "text", "push_name", "msg_id"}
  {"kind": "image", "session_id", "from", "caption", "push_name", "msg_id",
   "base64", "mimetype", "key"}     # base64 present only if webhookBase64 is on
  {"kind": "audio", "session_id", "from", "push_name", "msg_id",
   "base64", "mimetype", "key"}
Returns None for anything the bot must ignore: our own outgoing messages (fromMe),
group chats, status broadcasts/newsletters, reactions/protocol events, empty
messages, and duplicate deliveries (Evolution retries webhooks — dedupe by msg id).
"""
from collections import deque
from typing import Optional, Dict, Any

# Evolution retries a webhook if we're slow to ack, and reconnections can replay
# recent messages — remember the last N message ids so a customer is never answered twice.
_SEEN_MAX = 512
_seen_ids: deque = deque(maxlen=_SEEN_MAX)
_seen_set: set = set()


def _dedupe(msg_id: str) -> bool:
    """True if this message id was already processed."""
    if not msg_id:
        return False
    if msg_id in _seen_set:
        return True
    if len(_seen_ids) == _SEEN_MAX:
        _seen_set.discard(_seen_ids[0])
    _seen_ids.append(msg_id)
    _seen_set.add(msg_id)
    return False


def _unwrap(msg: Dict[str, Any]) -> Dict[str, Any]:
    """Peel ephemeral/viewOnce wrappers so the inner message types are visible."""
    for wrapper in ("ephemeralMessage", "viewOnceMessage", "viewOnceMessageV2",
                    "documentWithCaptionMessage"):
        inner = (msg.get(wrapper) or {}).get("message")
        if inner:
            return _unwrap(inner)
    return msg


def parse_incoming(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    event = (payload.get("event") or "").lower().replace("_", ".")
    if event and event != "messages.upsert":
        return None                                   # connection.update, qrcode.updated, ...

    data = payload.get("data") or payload
    if isinstance(data, list):                        # some versions batch: data = [items]
        data = data[0] if data else {}

    key = data.get("key") or {}
    if key.get("fromMe"):
        return None
    remote = key.get("remoteJid") or data.get("from") or ""
    if ("@g.us" in remote or "@broadcast" in remote or "@newsletter" in remote):
        return None                                   # groups / status / channels — customers only
    session_id = remote.split("@")[0].split(":")[0] if remote else "unknown"

    msg_id = key.get("id") or ""
    if _dedupe(msg_id):
        return None

    base = {
        "session_id": session_id,
        "from": remote,
        "push_name": (data.get("pushName") or "").strip(),
        "msg_id": msg_id,
        "key": key,
    }

    msg = _unwrap(data.get("message") or {})

    image = msg.get("imageMessage")
    if image:
        return {**base, "kind": "image",
                "caption": (image.get("caption") or "").strip(),
                "mimetype": image.get("mimetype") or "image/jpeg",
                # Evolution attaches the decoded media here when webhookBase64 is enabled
                "base64": msg.get("base64") or data.get("base64") or ""}

    audio = msg.get("audioMessage")
    if audio:
        return {**base, "kind": "audio",
                "mimetype": audio.get("mimetype") or "audio/ogg",
                "base64": msg.get("base64") or data.get("base64") or ""}

    text = (
        msg.get("conversation")
        or (msg.get("extendedTextMessage") or {}).get("text")
        or (msg.get("buttonsResponseMessage") or {}).get("selectedDisplayText")
        or (msg.get("listResponseMessage") or {}).get("title")
        or data.get("text")
        or ""
    )
    text = (text or "").strip()
    if not text:
        return None                                   # reactions, stickers, protocol events, ...
    return {**base, "kind": "text", "text": text}
