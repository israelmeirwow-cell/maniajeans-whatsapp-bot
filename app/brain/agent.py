"""The brain: classify intent -> route (human handoff or Claude tool-calling loop)."""
import re
import json
from typing import Dict, Any, Optional

from app.config import settings
from app.brain import intents as I
from app.brain.tools import TOOLS, dispatch
from app.brain.prompts import HANDOFF_MESSAGES
from app.brain import skills as SK
from app.memory.conversations import store as conv_store, Session
from app.handoff.escalation import escalate
from app.analytics.tagging import tag
from app.learning import lessons as L, feedback as F
from app.brain import sentiment as S
from app.brain import style as ST
from app.memory import customers as C

_client = None


def _get_client():
    global _client
    if _client is None and settings.has_claude:
        import anthropic
        _client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


def _card(p: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a catalog product into the fields the chat UI renders as a visual card."""
    return {"sku": str(p.get("sku") or ""), "name": p.get("name"),
            "price": p.get("price"), "price_was": p.get("price_was"),
            "on_sale": bool(p.get("on_sale")), "url": p.get("url"),
            "image": p.get("image"), "image_default": p.get("image_default"),
            "matched_color": p.get("matched_color"),
            "colors": p.get("colors", []), "sizes": p.get("sizes", [])}


# Persona: NO emojis at all (including ❤️). The model still leaks 🙂/😊/🖤/❤️ despite the
# prompt rule — strip every emoji deterministically so the rule always holds.
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF"
    "\U0001F1E6-\U0001F1FF\U0000FE00-\U0000FE0F\U0000200D\U00002190-\U000021FF]",
    flags=re.UNICODE,
)


def _sanitize(text: str) -> str:
    if not text:
        return text
    t = _EMOJI_RE.sub("", text)                  # strip ALL emojis, no exceptions
    # WhatsApp has no Markdown: **bold** renders as raw asterisks. Convert to WhatsApp's
    # native single-asterisk bold, and drop heading markers the model sometimes emits.
    t = re.sub(r"\*\*(.+?)\*\*", r"*\1*", t)
    t = re.sub(r"^#{1,4} ", "", t, flags=re.MULTILINE)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r" *\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t


def _shown_products_block(session: Session) -> str:
    """Let the bot answer follow-ups about products it already showed ('the 2nd one', 'that black
    shirt') and check real stock by sku — instead of re-searching or inventing an answer."""
    prods = getattr(session, "last_products", None)
    if not prods:
        return ""
    lines = []
    for i, p in enumerate(prods, 1):
        parts = [f'{i}. {p.get("name")}', f'מק"ט {p.get("sku")}', f'{p.get("price")}₪']
        if p.get("colors"):
            parts.append("צבעים: " + ", ".join(p["colors"]))
        if p.get("sizes"):
            parts.append("מידות: " + ", ".join(p["sizes"]))
        lines.append(" · ".join(str(x) for x in parts))
    return (
        '## מוצרים שכבר הצגת ללקוח בשיחה (לפי הסדר, עם מק"ט)\n'
        "כשהלקוח מתייחס אליהם (\"השני\", \"החולצה השחורה\", \"הראשון שהצגת\") — אלה המוצרים:\n"
        + "\n".join(lines)
        + '\n**זמינות מידה/צבע ספציפיים: חובה לקרוא ל-check_stock עם המק"ט לפני שקובעים שקיים/אזל. '
        "אל תמציא/י מלאי. אם הלקוח שאל על פריט שכבר הוצג — ענה/י ישירות, בלי לחפש שוב ובלי להציג "
        "שוב את כל הרשימה.**"
    )


def _extract_facts(session: Session, text: str) -> None:
    m = re.search(r"\b(\d{6})\b", text)                     # order ids are 6 digits in the demo
    if m:
        session.set_fact("order_id", m.group(1))
    m = re.search(r"\b(0\d[\d\-]{7,10})\b", text)           # Israeli phone
    if m:
        session.set_fact("phone", m.group(1))


def handle_message(session_id: str, text: str, force_bot: bool = False,
                   system_context: str = "") -> Dict[str, Any]:
    session = conv_store.get(session_id)
    session.add_user(text)
    _extract_facts(session, text)

    client = _get_client()
    who = {"user": "לקוח", "assistant": "נציג"}
    recent = "\n".join(f"{who[t['role']]}: {t['text'][:120]}" for t in session.turns[-5:-1])
    intent = I.classify(text, client, context=recent)
    session.tags.append(intent)

    # sentiment every turn (0 API cost) -> tone shift + escalation signal
    mood = S.analyze(text)
    session.sentiment = mood["level"]

    # No-LLM mode only: always-human intents get the (warm) templated handoff. WITH the LLM,
    # refund/discount flow through the model so the escalation is empathetic and non-repetitive
    # (the persona acknowledges naturally, asks for a photo on defects, then escalates).
    if client is None and intent in I.HUMAN_INTENTS and not force_bot:
        reason = {I.REFUND_RETURN: "זיכוי/החזר/מוצר פגום",
                  I.DISCOUNT: "בקשת הנחה/מחיר"}.get(intent, "פנייה לנציג")
        escalate(session, intent, reason)
        reply = HANDOFF_MESSAGES.get(intent, HANDOFF_MESSAGES["default"])
        session.add_assistant(reply)
        tag(session_id, intent, text, handoff=True, sentiment=mood["level"])
        return {"reply": reply, "intent": intent, "handoff": True, "sentiment": mood["level"]}

    # --- bot-served intents ---
    # Assemble per-turn context: lessons learned + tone (sentiment) + long-term customer memory.
    lesson_hits = L.retrieve(intent, text)
    style_hits = ST.retrieve(text)           # human-tone exemplars (style, never facts)
    extra_system = "\n\n".join(x for x in [
        L.as_prompt_block(lesson_hits),
        ST.as_prompt_block(style_hits),
        _shown_products_block(session),      # products shown earlier -> answer follow-ups by sku
        S.tone_note(mood["level"]),
        C.context_note(session_id),          # in WhatsApp, session_id == the customer's number
        system_context,                      # e.g. a photo's damage analysis
    ] if x)

    if client is None:
        reply, did, products = _fallback(session, intent)
    else:
        try:
            reply, did, products = _run_llm(client, session, intent, extra_system=extra_system)
        except Exception as e:
            # API unavailable (e.g. no credits/network) -> graceful non-LLM handling
            print(f"[agent] LLM unavailable, using fallback: {type(e).__name__}: {str(e)[:120]}")
            reply, did, products = _fallback(session, intent)

    reply = _sanitize(reply)                 # enforce persona: strip ALL emojis
    if products:
        session.last_products = products     # remember what we showed, for next-turn follow-ups
    session.add_assistant(reply)
    tag(session_id, intent, text, handoff=did, sentiment=mood["level"])
    return {"reply": reply, "intent": intent, "handoff": did,
            "lessons_applied": len(lesson_hits), "sentiment": mood["level"],
            "products": products}


def _merge_product_groups(search_groups, details):
    """Merge products to show as cards. One search → its top 8. Several searches in the
    same turn (an outfit: shirt + pants + shoes, or a refine) → top 3 of EACH group, so
    every category the bot searched is represented — never only the last search."""
    per = 8 if len(search_groups) <= 1 else 3
    out, seen = [], set()
    for group in search_groups:
        for card in group[:per]:
            if card["sku"] not in seen:
                seen.add(card["sku"])
                out.append(card)
    for card in details:
        if card["sku"] not in seen:
            seen.add(card["sku"])
            out.append(card)
    return out[:9]


def _run_llm(client, session: Session, intent: str, extra_system: str = ""):
    messages = session.anthropic_messages()
    # Skill-based prompt: core persona + only the playbook for THIS intent (not all seven).
    # Both blocks carry cache_control, so tools+core+skill are served from the prompt cache
    # (~90% cheaper, faster) on every turn after the first.
    system = SK.system_blocks(intent, extra_system)
    # Third breakpoint: the conversation history grows append-only, so caching up to the
    # newest user message makes each turn reuse the previous turn's history for free.
    if messages:
        last = messages[-1]
        if isinstance(last.get("content"), str):
            last["content"] = [{"type": "text", "text": last["content"],
                                "cache_control": {"type": "ephemeral"}}]
    did_handoff = False
    search_groups: list = []                   # cards per search_products call, in order
    detail_cards: list = []                    # cards from get_product_details lookups
    for _ in range(6):
        resp = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=1024,
            system=system,
            tools=TOOLS,
            messages=messages,
        )
        from app.analytics import costs
        costs.record_usage(settings.CLAUDE_MODEL, getattr(resp, "usage", None))
        if resp.stop_reason != "tool_use":
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
            return (text or HANDOFF_MESSAGES["default"], did_handoff,
                    _merge_product_groups(search_groups, detail_cards))

        # reconstruct assistant content + run tools
        assistant_content = []
        tool_results = []
        for b in resp.content:
            if getattr(b, "type", "") == "text":
                assistant_content.append({"type": "text", "text": b.text})
            elif getattr(b, "type", "") == "tool_use":
                assistant_content.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
                if b.name == "escalate_to_human":
                    did_handoff = True
                    escalate(session, intent, (b.input or {}).get("reason", "לא צוין"))
                    result = {"status": "escalated", "message": "השיחה הועברה לנציג אנושי בהצלחה"}
                else:
                    result = dispatch(b.name, b.input or {}, session_id=session.id)
                    # collect real product photos so the UI can show cards
                    if b.name == "search_products":
                        search_groups.append([_card(p) for p in (result.get("results") or [])
                                              if p.get("image") and p.get("sku")])
                    elif b.name == "get_product_details" and result.get("image"):
                        detail_cards.append(_card(result))
                tool_results.append({"type": "tool_result", "tool_use_id": b.id,
                                     "content": json.dumps(result, ensure_ascii=False)})
        messages.append({"role": "assistant", "content": assistant_content})
        messages.append({"role": "user", "content": tool_results})
    return (HANDOFF_MESSAGES["default"], did_handoff,
            _merge_product_groups(search_groups, detail_cards))


def _fallback(session: Session, intent: str):
    """No-API fallback so the app is testable without a key. Returns (reply, handoff, products)."""
    from app.woocommerce.client import store
    if intent == I.ORDER_STATUS:
        o = store.check_order_status(order_id=session.facts.get("order_id"),
                                     phone=session.facts.get("phone"))
        if not o.get("found"):
            return "אשמח לבדוק את סטטוס ההזמנה. מה מספר ההזמנה או הטלפון שאיתו בוצעה?", False, []
        if o.get("needs_handoff"):
            escalate(session, intent, f"הזמנה {o.get('order_id')} בסטטוס {o.get('status')}")
            return f"ההזמנה שלך בסטטוס '{o.get('status')}' — מעבירים אותך לנציג שיטפל בזה", True, []
        return f"סטטוס ההזמנה {o.get('order_id')}: {o.get('status')}", False, []
    if intent in (I.PRODUCT_INFO,):
        res = store.search_products(session.turns[-1]["text"], limit=6)
        if not res:
            return "לא מצאתי מוצר תואם. אפשר לתאר קצת אחרת מה חיפשת?", False, []
        cards = [_card(p) for p in res if p.get("image")]
        return "הנה כמה אפשרויות ששלפתי מהחנות:", False, cards
    if intent == I.CANCEL_ORDER:
        # The guarded cancel flow needs the LLM (multi-turn verify+confirm) — without it, human.
        escalate(session, intent, "בקשת ביטול הזמנה (מצב ללא LLM)")
        return HANDOFF_MESSAGES["cancel_order"], True, []
    if intent == I.FAQ:
        text = session.turns[-1]["text"]
        hits = [b for b in store.list_branches(limit=100) if b.get("city") and b["city"] in text]
        if hits or "סניף" in text or "שעות" in text:
            bs = hits or store.list_branches(limit=3)
            lines = []
            for b in bs[:3]:
                h = b.get("hours", {})
                lines.append(f"{b['name']} — {b.get('street', '')}\n"
                             f"   טל' {b.get('phone', '')}\n"
                             f"   א'–ה' {h.get('ראשון', '')} | ו' {h.get('שישי', '')} | שבת {h.get('שבת', '')}")
            return "\n".join(lines), False, []
        if "משלוח" in text or "משלוחים" in text:
            return "משלוחים:\n• שליח עד הבית — עד 7 ימי עסקים, 24.90₪\n• איסוף מנקודת חלוקה — עד 10 ימי עסקים, 14.90₪", False, []
        return store.get_policies("משלוח")[:400], False, []
    escalate(session, intent, "פנייה שלא זוהתה")
    return "לא בטוח שהבנתי בדיוק. מעביר אותך לנציג אנושי שיעזור לך", True, []


# ---------------- rich media (voice + image) ----------------
def handle_voice(session_id: str, audio_path: str) -> Dict[str, Any]:
    """Transcribe a voice note, then run it through the normal text pipeline."""
    from app.media import stt
    try:
        transcript = stt.transcribe(audio_path)
    except Exception as e:
        print(f"[agent] voice transcription failed: {type(e).__name__}: {str(e)[:120]}")
        transcript = ""
    if not transcript:
        return {"reply": "לא הצלחנו לשמוע את ההקלטה טוב. אפשר לשלוח שוב, או פשוט לכתוב לי?",
                "intent": "unrecognized", "handoff": False, "transcript": "", "input": "voice"}
    res = handle_message(session_id, transcript)
    res["transcript"] = transcript
    res["input"] = "voice"
    return res


def handle_image(session_id: str, image_path: str, caption: str = "") -> Dict[str, Any]:
    """Analyze a customer photo (damage verification) and respond in context."""
    from app.media import vision
    try:
        analysis = vision.analyze_damage(image_path, caption)
    except Exception as e:
        print(f"[agent] image handling failed: {type(e).__name__}: {str(e)[:120]}")
        analysis = {"ok": False}

    # Couldn't read/analyze the image -> friendly resend request, no LLM call, never an error.
    if not analysis.get("ok"):
        session = conv_store.get(session_id)
        session.add_user(caption.strip() or "[תמונה]")
        reply = ("קיבלנו את הקובץ אבל לא הצלחנו לפתוח את התמונה כמו שצריך.\n"
                 "אפשר לשלוח אותה שוב, רצוי כ-JPG וברור?")
        session.add_assistant(reply)
        return {"reply": reply, "intent": "unrecognized", "handoff": False,
                "vision": analysis, "input": "image"}

    ctx = (
        "## תמונה שהלקוח שלח — ניתוח אוטומטי מעוגן בקטלוג (התייחס/י אליו בלבד, אל תמציא/י):\n"
        f"- מסקנה: {analysis.get('summary', '')}\n"
        f"- is_our_product={analysis.get('is_our_product')}, "
        f"damage_visible={analysis.get('damage_visible')}, המלצה={analysis.get('recommendation')}\n"
        "- **אם is_our_product=no:** הפריט בתמונה אינו מסוג המוצרים שאנחנו מוכרים (אנחנו חנות "
        "אופנת גברים — ג'ינסים, חולצות, נעליים ואביזרים). ציין/י זאת בעדינות, ייתכן שנשלחה תמונה "
        "שגויה, ובקש/י תמונה של הפריט שנקנה אצלנו. אל תדון/י בפריט הזר כאילו הוא שלנו, ואל תאשר/י כלום.\n"
        "- אם is_our_product=yes ונזק ברור: הכר/י בנזק, הצטער/י, והעבר/י לנציג (escalate_to_human) "
        "לצורך החזר/משלוח חוזר — וציין/י בסיבת ההעברה שהנזק אומת מהתמונה.\n"
        "- אם is_our_product=yes אך אין נזק/לא ברור: בקש/י בעדינות תמונה ברורה יותר, אל תאשר/י החזר."
    )
    user_text = caption.strip() or "שלחתי תמונה של המוצר"
    res = handle_message(session_id, user_text, force_bot=True, system_context=ctx)
    res["vision"] = analysis
    res["input"] = "image"
    return res


# ---------------- learning loop (satisfaction + lessons) ----------------
def close_conversation(session_id: str, explicit: str = None) -> Dict[str, Any]:
    """End a conversation: assess satisfaction, and if it's worth learning from,
    distill a lesson automatically. `explicit` may be 'up'/'down' (customer rating)."""
    session = conv_store.get(session_id)
    client = _get_client()
    verdict = F.assess(session, client, explicit=explicit)
    lesson = None
    if verdict.get("lesson_worthy"):
        primary_intent = session.tags[-1] if session.tags else I.UNRECOGNIZED
        lesson = L.distill(session, primary_intent, client=client)

    # write long-term customer memory (survives across conversations)
    first_user = next((t["text"] for t in session.turns if t["role"] == "user"), "")
    primary = session.tags[-1] if session.tags else I.UNRECOGNIZED
    summary = f"{I._LABELS_HE.get(primary, primary)}: {first_user[:70]}"
    open_issue = summary if session.handoff else None
    C.log_conversation(session_id, summary, intents=session.tags, open_issue=open_issue)

    return {"verdict": verdict, "lesson": lesson}


def record_correction(session_id: str, correction: str) -> Dict[str, Any]:
    """A human agent supplies the correct answer for a conversation the bot mishandled;
    distill it into a lesson the bot will apply to similar future questions."""
    session = conv_store.get(session_id)
    client = _get_client()
    primary_intent = session.tags[-1] if session.tags else I.UNRECOGNIZED
    lesson = L.distill(session, primary_intent, correction=correction, client=client)
    return {"lesson": lesson}
