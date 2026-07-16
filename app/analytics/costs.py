"""API cost tracker — accumulates token usage per model and estimates USD.

Every Claude call site reports its usage here (`record`). Used by the customer simulator
to enforce a budget ceiling, and surfaced on the dashboard so the burn is visible.
Prices are per 1M tokens (Sonnet 5 shown at the intro rate valid through 2026-08-31).
"""
import json
import time
import threading
from typing import Dict, Any

from app.config import DATA_DIR

# (input $/Mtok, output $/Mtok) matched by model-name substring
_PRICES = [
    ("haiku", (1.0, 5.0)),
    ("sonnet", (2.0, 10.0)),   # Sonnet 5 intro pricing (through 2026-08-31); std is 3/15
    ("opus", (5.0, 25.0)),
    ("fable", (10.0, 50.0)),
]
_DEFAULT = (3.0, 15.0)

_lock = threading.Lock()
_usage: Dict[str, Dict[str, int]] = {}   # model -> {input, output, calls} (this process, all-time)
_USAGE_LOG = DATA_DIR / "logs" / "usage.jsonl"   # persisted per-call, for cross-process weekly report


def _price(model: str):
    m = (model or "").lower()
    for key, p in _PRICES:
        if key in m:
            return p
    return _DEFAULT


def record(model: str, input_tokens: int = 0, output_tokens: int = 0,
           cache_read: int = 0, cache_write: int = 0) -> None:
    """Cache tokens bill differently (reads ~0.1x the input price, writes ~1.25x) —
    tracked separately so the dashboard stays honest now that prompt caching is on."""
    input_tokens, output_tokens = int(input_tokens or 0), int(output_tokens or 0)
    cache_read, cache_write = int(cache_read or 0), int(cache_write or 0)
    with _lock:
        u = _usage.setdefault(model, {"input": 0, "output": 0, "calls": 0,
                                      "cache_read": 0, "cache_write": 0})
        u.setdefault("cache_read", 0)
        u.setdefault("cache_write", 0)
        u["input"] += input_tokens
        u["output"] += output_tokens
        u["cache_read"] += cache_read
        u["cache_write"] += cache_write
        u["calls"] += 1
    # persist per-call so the weekly report can sum real spend across processes/restarts
    try:
        pin, pout = _price(model)
        usd = (input_tokens / 1e6 * pin + output_tokens / 1e6 * pout
               + cache_read / 1e6 * pin * 0.1 + cache_write / 1e6 * pin * 1.25)
        _USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _USAGE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "model": model,
                                "input": input_tokens, "output": output_tokens,
                                "cache_read": cache_read, "cache_write": cache_write,
                                "usd": round(usd, 6)}, ensure_ascii=False) + "\n")
    except Exception:
        pass   # never let cost logging break a request


def record_usage(model: str, usage) -> None:
    """Convenience: record straight from an Anthropic response.usage object."""
    if usage is None:
        return
    record(model, getattr(usage, "input_tokens", 0), getattr(usage, "output_tokens", 0),
           cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0,
           cache_write=getattr(usage, "cache_creation_input_tokens", 0) or 0)


def model_usd(model: str) -> float:
    u = _usage.get(model, {})
    pin, pout = _price(model)
    return (u.get("input", 0) / 1e6 * pin + u.get("output", 0) / 1e6 * pout
            + u.get("cache_read", 0) / 1e6 * pin * 0.1
            + u.get("cache_write", 0) / 1e6 * pin * 1.25)


def total_usd() -> float:
    return sum(model_usd(m) for m in _usage)


def summary() -> Dict[str, Any]:
    with _lock:
        per_model = {}
        for m, u in _usage.items():
            per_model[m] = {"calls": u["calls"], "input": u["input"],
                            "output": u["output"], "usd": round(model_usd(m), 4)}
        return {"total_usd": round(total_usd(), 4),
                "calls": sum(u["calls"] for u in _usage.values()),
                "by_model": per_model}


def spend_since(start_ts: str) -> Dict[str, Any]:
    """Sum persisted API spend since start_ts ('YYYY-mm-dd HH:MM:SS'), from usage.jsonl.
    Returns {usd, calls, by_model:{model:{calls,usd,input,output}}} — used by the weekly report."""
    total, calls, by_model = 0.0, 0, {}
    if _USAGE_LOG.is_file():
        for line in _USAGE_LOG.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(r.get("ts", "")) < start_ts:
                continue
            usd = float(r.get("usd", 0) or 0)
            total += usd
            calls += 1
            m = by_model.setdefault(r.get("model", "?"),
                                    {"calls": 0, "usd": 0.0, "input": 0, "output": 0})
            m["calls"] += 1
            m["usd"] += usd
            m["input"] += int(r.get("input", 0) or 0)
            m["output"] += int(r.get("output", 0) or 0)
    for m in by_model.values():
        m["usd"] = round(m["usd"], 4)
    return {"usd": round(total, 4), "calls": calls, "by_model": by_model}


def reset() -> None:
    with _lock:
        _usage.clear()
