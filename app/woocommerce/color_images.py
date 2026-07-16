"""Per-color product images.

The scraped catalog stored ONE image per product (usually the first/default color), so a
"black shirt" search would show the default (often white) photo. Magento product pages embed a
`jsonConfig` that maps each color option to its own image; this module parses it and keeps a
`sku -> {color_label: image_url}` cache in data/store/color_images.json (built by the background
enrichment script). At runtime `image_for(sku, color)` returns the color-specific photo if known.
"""
import json
import time
from typing import Dict, Optional

from app.config import STORE_DIR

from app.config import store_file
_CACHE_FILE = store_file("color_images.json")   # real cache, else committed sample ({})
_IMAGES: Dict[str, Dict[str, str]] = {}
_mtime = 0.0
_last_check = 0.0


def _load() -> None:
    global _IMAGES, _mtime
    if _CACHE_FILE.is_file():
        try:
            _IMAGES = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            _mtime = _CACHE_FILE.stat().st_mtime
        except (json.JSONDecodeError, OSError):
            _IMAGES = {}


_load()


def _maybe_reload() -> None:
    """Cheaply pick up the enrichment job's progress on a live server (throttled, mtime-gated)."""
    global _last_check
    now = time.time()
    if now - _last_check < 20:
        return
    _last_check = now
    try:
        if _CACHE_FILE.stat().st_mtime != _mtime:
            _load()
    except OSError:
        pass


def reload() -> int:
    """Force re-read the cache from disk. Returns #products known."""
    _load()
    return len(_IMAGES)


def image_for(sku: str, color: Optional[str]) -> Optional[str]:
    """The color-specific image for this sku+color, or None if not enriched yet."""
    if not color:
        return None
    _maybe_reload()
    return _IMAGES.get(str(sku), {}).get(color)


# ---------------- parsing (used by the enrichment script) ----------------
def _extract_json_after(marker: str, s: str) -> Optional[str]:
    """Return the balanced-brace JSON object that follows `marker` in `s`."""
    i = s.find(marker)
    if i < 0:
        return None
    i = s.find("{", i)
    if i < 0:
        return None
    depth, j, instr, esc = 0, i, False, False
    while j < len(s):
        ch = s[j]
        if instr:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                instr = False
        else:
            if ch == '"':
                instr = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return s[i:j + 1]
        j += 1
    return None


def color_images_from_html(html: str) -> Dict[str, str]:
    """Parse a Magento product page's jsonConfig into {color_label: image_url}."""
    raw = _extract_json_after('"jsonConfig":', html)
    if not raw:
        return {}
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    attrs, imgs = cfg.get("attributes", {}), cfg.get("images", {})
    out: Dict[str, str] = {}
    for a in attrs.values():
        if a.get("code") != "color":
            continue
        for opt in a.get("options", []):
            pr = opt.get("products", {})
            vids = (pr.get("stock", []) + pr.get("outofstock", [])) if isinstance(pr, dict) else (pr or [])
            for vid in vids:
                gal = imgs.get(vid) or []
                if gal:
                    url = gal[0].get("full") or gal[0].get("img")
                    if url:
                        out[opt["label"]] = url
                        break
    return out
