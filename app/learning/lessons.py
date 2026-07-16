"""The learning memory — distilled 'lessons' the bot consults on future messages.

A lesson is a compact rule learned from a mistake, a gap, or a human correction:
    {intent, trigger, guidance, keywords, source}

On each new message the agent RETRIEVES the most relevant lessons and injects them
into the prompt, so the bot improves over time WITHOUT retraining the model.
This is continual learning via retrieved memory (not model fine-tuning).
"""
import os
import re
import json
import time
from typing import List, Dict, Any, Optional

import numpy as np

from app.config import settings, DATA_DIR
from app.woocommerce.client import _norm            # reuse the Hebrew normalizer
from app.learning.embeddings import get_embedder

_FILE = DATA_DIR / "learning" / "lessons.jsonl"
# Precision-first gate: a lesson is injected only if the semantic match is STRONG,
# or MODERATE *and* corroborated by a keyword/intent overlap. Avoids injecting a wrong
# lesson (which could mislead the bot) when the small local model is merely lukewarm.
_STRONG_SIM = float(os.environ.get("EMBEDDINGS_STRONG_SIM", "0.42"))
_WEAK_SIM = float(os.environ.get("EMBEDDINGS_MIN_SIM", "0.30"))


def _load() -> List[Dict[str, Any]]:
    if not _FILE.is_file():
        return []
    out = []
    for line in _FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def add_lesson(intent: str, trigger: str, guidance: str,
               keywords: Optional[List[str]] = None, source: str = "manual") -> Dict[str, Any]:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "intent": intent, "trigger": trigger, "guidance": guidance,
        "keywords": keywords or [], "source": source,
    }
    with _FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def retrieve(intent: str, text: str, limit: int = 3) -> List[Dict[str, Any]]:
    """Most relevant lessons for this message. Uses semantic (embeddings) retrieval
    when an embedder is available, else falls back to keyword matching."""
    emb = get_embedder()
    if emb.available:
        try:
            return _semantic_retrieve(emb, intent, text, limit)
        except Exception as e:
            print(f"[lessons] semantic retrieve failed, keyword fallback: {type(e).__name__}")
    return _keyword_retrieve(intent, text, limit)


def _keyword_retrieve(intent: str, text: str, limit: int) -> List[Dict[str, Any]]:
    hay = _norm(text)
    scored = []
    for l in _load():
        score = 0
        if l.get("intent") == intent:
            score += 2
        for k in l.get("keywords", []):
            nk = _norm(k)
            if nk and nk in hay:
                score += 2
        for w in _norm(l.get("trigger", "")).split():
            if len(w) >= 3 and w in hay:
                score += 1
        if score > 0:
            scored.append((score, l))
    scored.sort(key=lambda x: -x[0])
    return [l for _, l in scored[:limit]]


def _lesson_text(l: Dict[str, Any]) -> str:
    return " · ".join(x for x in [l.get("trigger", ""), l.get("guidance", ""),
                                  " ".join(l.get("keywords", []))] if x)


# cache lesson vectors so we don't re-embed the whole file on every message
_vec_cache: Dict[str, Any] = {"key": None, "vecs": None}


def _lesson_matrix(emb, lessons: List[Dict[str, Any]]) -> np.ndarray:
    texts = [_lesson_text(l) for l in lessons]
    key = hash(tuple(texts))
    if _vec_cache["key"] != key:
        _vec_cache["key"] = key
        _vec_cache["vecs"] = emb.embed(texts, is_query=False)
    return _vec_cache["vecs"]


def _semantic_retrieve(emb, intent: str, text: str, limit: int) -> List[Dict[str, Any]]:
    """Hybrid: cosine similarity (semantic) + small lexical/intent boosts, then floor."""
    lessons = _load()
    if not lessons:
        return []
    mat = _lesson_matrix(emb, lessons)              # (n, d), normalized
    q = emb.embed([text], is_query=True)[0]         # (d,), normalized
    sims = mat @ q                                   # cosine similarity
    hay = _norm(text)
    kept = []
    for i, l in enumerate(lessons):
        sim = float(sims[i])
        intent_match = l.get("intent") == intent
        kw_hit = any(_norm(k) and _norm(k) in hay for k in l.get("keywords", []))
        # A lukewarm match is rescued only by a real keyword overlap — intent is too
        # coarse to corroborate (every FAQ would match a FAQ lesson). Intent only ranks.
        if sim >= _STRONG_SIM or (sim >= _WEAK_SIM and kw_hit):
            kept.append((sim + (0.05 if intent_match else 0.0), l))
    kept.sort(key=lambda x: -x[0])
    return [l for _, l in kept][:limit]


def as_prompt_block(lessons: List[Dict[str, Any]]) -> str:
    if not lessons:
        return ""
    lines = ["## לקחים מניסיון קודם (למד/י מהם והחל/י אותם עכשיו):"]
    for l in lessons:
        lines.append(f"- כשלקוח {l.get('trigger', '').strip()} → {l.get('guidance', '').strip()}")
    return "\n".join(lines)


# ---- distillation: turn a bad/edge conversation (+ optional human correction) into a lesson ----
_DISTILL_SYS = (
    "מתוך שיחת שירות לקוחות שבה הבוט טעה, לא נתן מענה טוב, או היה בה פער ידע "
    "(ואולי צורף תיקון/תשובה נכונה מנציג אנושי) — נסח/י לקח קצר לשיפור עתידי. "
    "כלל קריטי: אם הנציג סיפק **עובדה או תשובה קונקרטית** (למשל תנאי אחריות, מחיר, "
    "מדיניות), ה-guidance חייב לכלול את העובדה עצמה **במלואה ובמדויק**, כדי שבפעם הבאה "
    "הבוט יענה ישירות. אל תנסח הנחיה כללית כמו 'תבדוק ותחזור' כשיש בידך תשובה עובדתית. "
    "רק אם אין תשובה עובדתית — נסח/י הנחיה התנהגותית. "
    "החזר/י JSON בלבד ותו לא: "
    '{"trigger": "מתי המצב הזה קורה, בקצרה", "guidance": "התשובה/העובדה המדויקת לשימוש חוזר, או ההנחיה", '
    '"keywords": ["מילת_מפתח", "..."]}'
)


def distill(session, intent: str, correction: Optional[str] = None, client=None) -> Optional[Dict[str, Any]]:
    if client is None or not settings.has_claude:
        return None
    content = session.transcript()
    if correction:
        content += f"\n\nהתשובה/התיקון הנכון מהנציג האנושי: {correction}"
    try:
        resp = client.messages.create(
            model=settings.CLAUDE_CLASSIFIER_MODEL, max_tokens=280,
            system=_DISTILL_SYS, messages=[{"role": "user", "content": content}])
        from app.analytics import costs
        costs.record_usage(settings.CLAUDE_CLASSIFIER_MODEL, getattr(resp, "usage", None))
        txt = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        m = re.search(r"\{.*\}", txt, re.S)
        if not m:
            return None
        d = json.loads(m.group(0))
        return add_lesson(intent, d.get("trigger", ""), d.get("guidance", ""),
                          d.get("keywords", []),
                          source="human_correction" if correction else "distilled")
    except Exception:
        return None
