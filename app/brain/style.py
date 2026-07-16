"""Style exemplars — retrieval-augmented TONE.

A Hebrew style bank (data/style/style_bank.jsonl, distilled from the Bitext dataset into
Israeli-WhatsApp Hebrew) holds pairs of (customer line in some register) -> (exemplary human
sales-rep reply, facts as placeholders only). On each message we retrieve the closest pair
and inject it as a few-shot example, so the bot MIRRORS how a real human rep sounds —
matching the customer's register (slang→loose, polite→polite) — while facts still come
exclusively from the tools.
"""
import json
from typing import List, Dict, Any

import numpy as np

from app.config import DATA_DIR
from app.learning.embeddings import get_embedder

_FILE = DATA_DIR / "style" / "style_bank.jsonl"
_MIN_SIM = 0.25   # tone exemplars are low-risk (no facts), so a lower floor than lessons is fine

_bank: List[Dict[str, Any]] = []
_matrix = None


def _load():
    global _bank, _matrix
    if _bank:
        return
    if not _FILE.is_file():
        return
    _bank = [json.loads(l) for l in _FILE.read_text(encoding="utf-8").splitlines() if l.strip()]


def _ensure_matrix(emb):
    global _matrix
    if _matrix is None and _bank:
        _matrix = emb.embed([r["customer"] for r in _bank], is_query=False)
    return _matrix


def retrieve(text: str, limit: int = 2) -> List[Dict[str, Any]]:
    _load()
    if not _bank:
        return []
    emb = get_embedder()
    if not emb.available:
        return []
    try:
        mat = _ensure_matrix(emb)
        q = emb.embed([text], is_query=True)[0]
        sims = mat @ q
        order = np.argsort(-sims)[:limit]
        return [_bank[i] for i in order if float(sims[i]) >= _MIN_SIM]
    except Exception as e:
        print(f"[style] retrieval failed: {type(e).__name__}")
        return []


def as_prompt_block(exemplars: List[Dict[str, Any]]) -> str:
    if not exemplars:
        return ""
    lines = ["## כך נשמע נציג אנושי במצב דומה (חקה את הסגנון והחום — לא את התוכן!",
             "העובדות שלך מגיעות אך ורק מהכלים; ה-placeholders כאן הם רק הדגמה):"]
    for ex in exemplars:
        lines.append(f'- לקוח: "{ex["customer"]}"')
        lines.append(f'  נציג: "{ex["reply"]}"')
    return "\n".join(lines)
