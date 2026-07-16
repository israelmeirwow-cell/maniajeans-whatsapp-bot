"""WhatsApp layer tests: webhook parsing, media materialization, gateway payloads,
and the full inbound→brain→outbound pipeline (with the brain and HTTP mocked).

Run:  python3 -m pytest tests/ -q
"""
import base64
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.whatsapp import webhook as WH
from app.whatsapp import gateway as GW
from app.whatsapp import media as M
from app.config import settings


def _upsert(msg_id, message, remote="972501234567@s.whatsapp.net",
            from_me=False, push_name="דני"):
    return {"event": "messages.upsert",
            "instance": "maniajeans",
            "data": {"key": {"remoteJid": remote, "fromMe": from_me, "id": msg_id},
                     "pushName": push_name,
                     "message": message,
                     "messageTimestamp": 1750000000}}


# ---------------- webhook parsing ----------------

def test_parse_plain_text():
    p = WH.parse_incoming(_upsert("m1", {"conversation": "מה הסטטוס של הזמנה 100235?"}))
    assert p["kind"] == "text"
    assert p["session_id"] == "972501234567"
    assert p["text"] == "מה הסטטוס של הזמנה 100235?"
    assert p["push_name"] == "דני"


def test_parse_extended_text():
    p = WH.parse_incoming(_upsert("m2", {"extendedTextMessage": {"text": "יש מבצעים?"}}))
    assert p["kind"] == "text" and p["text"] == "יש מבצעים?"


def test_parse_image_with_base64_and_caption():
    b64 = base64.b64encode(b"fakejpegbytes").decode()
    msg = {"imageMessage": {"caption": "המכנס הגיע קרוע", "mimetype": "image/jpeg"},
           "base64": b64}
    p = WH.parse_incoming(_upsert("m3", msg))
    assert p["kind"] == "image"
    assert p["caption"] == "המכנס הגיע קרוע"
    assert p["base64"] == b64


def test_parse_audio():
    p = WH.parse_incoming(_upsert("m4", {"audioMessage": {"mimetype": "audio/ogg; codecs=opus"}}))
    assert p["kind"] == "audio"
    assert "ogg" in p["mimetype"]


def test_ignores_own_and_group_and_status_messages():
    assert WH.parse_incoming(_upsert("m5", {"conversation": "x"}, from_me=True)) is None
    assert WH.parse_incoming(_upsert("m6", {"conversation": "x"},
                                     remote="1203633@g.us")) is None
    assert WH.parse_incoming(_upsert("m7", {"conversation": "x"},
                                     remote="status@broadcast")) is None


def test_ignores_non_message_events_and_empty():
    assert WH.parse_incoming({"event": "connection.update", "data": {"state": "open"}}) is None
    assert WH.parse_incoming(_upsert("m8", {"reactionMessage": {"text": "👍"}})) is None


def test_dedupes_redelivered_message():
    assert WH.parse_incoming(_upsert("dup1", {"conversation": "שלום"})) is not None
    assert WH.parse_incoming(_upsert("dup1", {"conversation": "שלום"})) is None


def test_unwraps_ephemeral():
    inner = {"ephemeralMessage": {"message": {"conversation": "הודעה נעלמת"}}}
    p = WH.parse_incoming(_upsert("m9", inner))
    assert p["kind"] == "text" and p["text"] == "הודעה נעלמת"


# ---------------- media materialization ----------------

def test_save_incoming_from_inline_base64():
    b64 = base64.b64encode(b"\xff\xd8\xff imagebytes").decode()
    path = M.save_incoming({"kind": "image", "base64": b64, "mimetype": "image/jpeg"})
    try:
        assert path.endswith(".jpg")
        assert Path(path).read_bytes().startswith(b"\xff\xd8\xff")
    finally:
        os.unlink(path)


def test_save_incoming_strips_data_prefix():
    b64 = "data:image/png;base64," + base64.b64encode(b"pngbytes").decode()
    path = M.save_incoming({"kind": "image", "base64": b64, "mimetype": "image/png"})
    try:
        assert Path(path).read_bytes() == b"pngbytes"
    finally:
        os.unlink(path)


def test_save_incoming_no_media_returns_none(monkeypatch):
    monkeypatch.setattr(GW, "fetch_media_base64", lambda key: None)
    assert M.save_incoming({"kind": "image", "base64": "", "key": {"id": "x"}}) is None


# ---------------- gateway ----------------

@pytest.fixture
def fake_gateway(monkeypatch):
    """Configure the gateway and capture outgoing HTTP calls instead of sending them."""
    monkeypatch.setattr(settings, "WA_GATEWAY_URL", "http://evo.local:8080", raising=False)
    monkeypatch.setattr(settings, "WA_GATEWAY_API_KEY", "test-key", raising=False)
    monkeypatch.setattr(settings, "WA_INSTANCE_NAME", "maniajeans", raising=False)
    calls = []

    class FakeResp:
        status_code = 201
        def json(self):
            return {"status": "ok"}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers})
        return FakeResp()

    monkeypatch.setattr(GW.httpx, "post", fake_post)
    return calls


def test_send_message_payload(fake_gateway):
    r = GW.send_message("972501234567@s.whatsapp.net", "שלום!")
    assert r["sent"] is True
    call = fake_gateway[0]
    assert call["url"] == "http://evo.local:8080/message/sendText/maniajeans"
    assert call["json"] == {"number": "972501234567", "text": "שלום!"}
    assert call["headers"]["apikey"] == "test-key"


def test_send_unconfigured_is_noop(monkeypatch):
    monkeypatch.setattr(settings, "WA_GATEWAY_URL", "", raising=False)
    r = GW.send_message("972501234567", "שלום")
    assert r["sent"] is False and "not configured" in r["reason"]


def test_send_product_cards_uses_fetched_base64(fake_gateway):
    products = [{"sku": "1", "name": "ג'ינס DORI", "price": 199, "on_sale": True,
                 "price_was": 299, "url": "https://store/p1", "image": "https://store/i1.jpg"},
                {"sku": "2", "name": "בלי תמונה", "price": 50}]
    sent = GW.send_product_cards("972501234567", products,
                                 fetch_image=lambda u: (b"jpegbytes", "image/jpeg"))
    assert sent == 1                       # the imageless product is skipped
    body = fake_gateway[0]["json"]
    assert body["mediatype"] == "image"
    assert body["media"] == base64.b64encode(b"jpegbytes").decode()
    assert "199₪ במקום 299₪" in body["caption"] and "https://store/p1" in body["caption"]


def test_set_webhook_registers_secret_path(fake_gateway, monkeypatch):
    monkeypatch.setattr(settings, "WA_WEBHOOK_SECRET", "s3cret", raising=False)
    GW.set_webhook("https://bot.example.com")
    body = fake_gateway[0]["json"]
    assert body["webhook"]["url"] == "https://bot.example.com/webhook/s3cret"
    assert body["webhook"]["base64"] is True
    assert body["webhook"]["events"] == ["MESSAGES_UPSERT"]


# ---------------- end-to-end: webhook → brain → WhatsApp reply ----------------

def test_pipeline_text_message_replies_on_whatsapp(monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main

    sent = []
    monkeypatch.setattr(settings, "WA_WEBHOOK_SECRET", "", raising=False)
    monkeypatch.setattr(main, "handle_message",
                        lambda sid, text: {"reply": f"בוט: קיבלתי '{text}'", "intent": "faq",
                                           "handoff": False, "products": []})
    monkeypatch.setattr(GW, "send_typing", lambda to, ms=2500: {"sent": True})
    monkeypatch.setattr(GW, "send_message",
                        lambda to, text: (sent.append((to, text)), {"sent": True})[1])
    monkeypatch.setattr(GW, "send_product_cards", lambda *a, **k: 0)

    client = TestClient(main.app)
    res = client.post("/webhook", json=_upsert("e2e-1", {"conversation": "מה שעות הפתיחה?"}))
    assert res.json() == {"ok": True}
    assert sent == [("972501234567", "בוט: קיבלתי 'מה שעות הפתיחה?'")]


def test_pipeline_secured_webhook_rejects_bad_token(monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main
    monkeypatch.setattr(settings, "WA_WEBHOOK_SECRET", "s3cret", raising=False)
    client = TestClient(main.app)
    res = client.post("/webhook/wrong", json=_upsert("e2e-2", {"conversation": "היי"}))
    assert res.json()["ignored"] is True
    res = client.post("/webhook", json=_upsert("e2e-3", {"conversation": "היי"}))
    assert res.json()["ignored"] is True


def test_pipeline_handoff_notifies_agent(monkeypatch):
    """A refund intent must escalate AND ping the human agent's WhatsApp in real time."""
    from app.memory.conversations import store as conv_store
    from app.handoff.escalation import escalate

    monkeypatch.setattr(settings, "HANDOFF_NOTIFY", "972509999999", raising=False)
    pings = []
    monkeypatch.setattr(GW, "notify_agent", lambda summary: (pings.append(summary), {"sent": True})[1])

    conv_store.reset("972501111111")
    s = conv_store.get("972501111111")
    s.add_user("קיבלתי מוצר פגום ואני רוצה החזר")
    escalate(s, "refund_return", "מוצר פגום")
    assert len(pings) == 1
    assert "העברה לנציג אנושי" in pings[0]
    assert "מוצר פגום" in pings[0]
    assert s.handoff is True
