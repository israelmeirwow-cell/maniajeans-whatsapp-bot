"""Store data access.

DEMO: reads the static local snapshot scraped from Mania Jeans (data/store/*).
The public method surface is what the agent tools call; swapping to a live
WooCommerce store later means reimplementing these methods against the WC REST
API (read-only) — the agent/tools above do not change.
"""
import re
import json
import datetime as dt
from pathlib import Path
from typing import List, Dict, Optional, Any

from app.config import STORE_DIR, settings
from app.woocommerce import color_images

# Common Hebrew filler words to ignore when searching (so "יש לכם ג'ינס" -> "ג'ינס").
_STOPWORDS = {
    "יש", "לכם", "לכן", "את", "זה", "של", "לי", "אני", "רוצה", "רוצם", "האם", "עם",
    "או", "גם", "מה", "אפשר", "צריך", "בבקשה", "היי", "שלום", "כמה", "עולה", "יש לכם",
    "אתם", "מוכרים", "צריכה", "מחפש", "מחפשת", "לגבר", "לגברים",
}


def _norm(s: str) -> str:
    return (s or "").replace("'", "").replace("־", "-").strip().lower()


# Hebrew color adjective forms (m/f, sg/pl) + English → canonical catalog root. Matching is by
# substring so compound catalog colors ("שחור מנוקד", "כחול כהה") still hit.
_COLOR_FORMS = {
    "שחור": ("שחור", "שחורה", "שחורים", "שחורות", "black"),
    "לבן": ("לבן", "לבנה", "לבנים", "לבנות", "white"),
    "אפור": ("אפור", "אפורה", "אפורים", "אפורות", "gray", "grey"),
    "כחול": ("כחול", "כחולה", "כחולים", "כחולות", "נייבי", "navy", "blue"),
    "אדום": ("אדום", "אדומה", "אדומים", "אדומות", "red"),
    "ירוק": ("ירוק", "ירוקה", "ירוקים", "ירוקות", "זית", "חאקי", "khaki", "green"),
    "צהוב": ("צהוב", "צהובה", "צהובים", "yellow"),
    "כתום": ("כתום", "כתומה", "orange"),
    "ורוד": ("ורוד", "ורודה", "pink"),
    "סגול": ("סגול", "סגולה", "purple"),
    "חום": ("חום", "חומה", "חומים", "brown"),
    "בז": ("בז", "בזי", "קרם", "beige", "cream"),
    "בורדו": ("בורדו", "bordeaux", "wine"),
    "טורקיז": ("טורקיז", "turquoise"),
    "חרדל": ("חרדל", "mustard"),
    "כסף": ("כסף", "כסוף", "silver"),
    "זהב": ("זהב", "זהוב", "gold"),
}
_ALL_COLOR_FORMS = {f for forms in _COLOR_FORMS.values() for f in forms}


def _wanted_colors(query: str) -> List[str]:
    """Canonical color roots the customer asked for (empty if none)."""
    qn = _norm(query)
    toks = set(re.sub(r"[^\w֐-׿ ]", " ", qn).split())
    roots = []
    for root, forms in _COLOR_FORMS.items():
        if (toks & set(forms)) or any(len(f) >= 4 and f in qn for f in forms):
            roots.append(root)
    return roots


def _match_color(product_colors: List[str], wanted_roots: List[str]) -> Optional[str]:
    """Return the product's color string that satisfies a requested root, else None."""
    for c in product_colors:
        cn = _norm(c)
        for root in wanted_roots:
            if root in cn:
                return c
    return None


def _tokens(q: str) -> List[str]:
    q = re.sub(r"[^\w֐-׿ ]", " ", _norm(q))
    return [t for t in q.split() if len(t) >= 2 and t not in _STOPWORDS]


def _stem(t: str) -> str:
    """Crude Hebrew plural/suffix stripper so 'חולצות' matches 'חולצה'."""
    for suf in ("יים", "ות", "ים", "י"):
        if t.endswith(suf) and len(t) - len(suf) >= 2:
            return t[: -len(suf)]
    return t


def _hit(token: str, hay: str) -> bool:
    return token in hay or _stem(token) in hay


class StoreClient:
    def __init__(self, store_dir: Path = STORE_DIR):
        self.store_dir = store_dir
        from app.config import store_file      # real file, else its .sample twin
        self._catalog: List[Dict[str, Any]] = json.loads(store_file("catalog.json").read_text(encoding="utf-8"))
        self._branches: List[Dict[str, Any]] = json.loads(store_file("branches.json").read_text(encoding="utf-8"))
        self._orders: List[Dict[str, Any]] = json.loads(store_file("demo_orders.json").read_text(encoding="utf-8"))
        self._policies: str = store_file("policies.md").read_text(encoding="utf-8")
        self._by_sku = {str(p.get("sku")): p for p in self._catalog}

    # ---------- products ----------
    def search_products(self, query: str, on_sale: Optional[bool] = None,
                        max_price: Optional[float] = None, limit: int = 6) -> List[Dict[str, Any]]:
        wanted_colors = _wanted_colors(query)                       # e.g. "חולצה שחורה" -> ["שחור"]
        tokens = [t for t in _tokens(query) if t not in _ALL_COLOR_FORMS]  # score on non-color words
        scored = []
        for p in self._catalog:
            if on_sale is not None and bool(p.get("on_sale")) != on_sale:
                continue
            price = p.get("price")
            if max_price is not None and isinstance(price, (int, float)) and price > max_price:
                continue
            # color filter: if the customer asked for a color, only keep items available in it
            matched_color = None
            if wanted_colors:
                matched_color = _match_color(p.get("colors", []), wanted_colors)
                if not matched_color:
                    continue
            name_n = _norm(p.get("name", ""))
            hay = name_n + " " + _norm(p.get("category_path", ""))
            # The product TYPE is the head of both the query and the catalog name in Hebrew
            # ("נעליים קלאסיות", "נעלי עור תוני") — so a type match must dominate the score,
            # otherwise "נעליים קלאסיות" ranks a "עניבה קלאסית" (adjective hit) above shoes.
            head = " ".join(name_n.split()[:2])
            if not tokens:
                score = 0
            else:
                score = 0
                for i, t in enumerate(tokens):
                    if _hit(t, head):
                        score += 8 if i == 0 else 3   # query's first word = the wanted type
                    elif _hit(t, hay):
                        score += 1                    # descriptive word (fit, style, category)
                if score == 0:
                    continue
                if _norm(query) in hay:
                    score += 2
            if matched_color:
                score += 1
            scored.append((score, price if isinstance(price, (int, float)) else 9e9, p, matched_color))
        scored.sort(key=lambda x: (-x[0], x[1]))
        # If the wanted product type matched anywhere, drop cross-category noise (items that
        # matched only on an adjective) — a shoes search must never return ties and shirts.
        if scored and scored[0][0] >= 8:
            scored = [s for s in scored if s[0] >= 8]
        seen, out = set(), []
        for _, _, p, matched_color in scored:
            key = _norm(p.get("name", ""))
            if key in seen:
                continue
            seen.add(key)
            card = self._compact(p)
            if matched_color:
                card["matched_color"] = matched_color     # the color the customer asked for
                cimg = color_images.image_for(p.get("sku"), matched_color)
                if cimg and cimg != card.get("image"):
                    card["image_default"] = card.get("image")   # base photo = fallback if color 404s
                    card["image"] = cimg                        # show the requested color's photo
            out.append(card)
            if len(out) >= limit:
                break
        return out

    def get_product(self, sku: str) -> Optional[Dict[str, Any]]:
        p = self._by_sku.get(str(sku))
        return self._full(p) if p else None

    def check_stock(self, sku: str, color: Optional[str] = None,
                    size: Optional[str] = None) -> Dict[str, Any]:
        p = self._by_sku.get(str(sku))
        if not p:
            return {"found": False}
        colors = p.get("colors", [])
        sizes = p.get("sizes", [])
        res = {"found": True, "sku": sku, "name": p.get("name"),
               "availability": p.get("availability"), "colors": colors, "sizes": sizes}
        if color or size:
            match = [v for v in p.get("variants", [])
                     if (color is None or _norm(v.get("color")) == _norm(color))
                     and (size is None or _norm(v.get("size")) == _norm(size))]
            res["variant_available"] = len(match) > 0
        return res

    # ---------- orders (demo mock) ----------
    def check_order_status(self, order_id: Optional[str] = None,
                           phone: Optional[str] = None) -> Dict[str, Any]:
        order = None
        for o in self._orders:
            if order_id and str(o.get("order_id")) == str(order_id).strip():
                order = o
                break
            if phone and _digits(o.get("phone")) == _digits(phone):
                order = o
                break
        if not order:
            return {"found": False}
        out = dict(order)
        out["found"] = True
        out["needs_handoff"] = self._order_needs_handoff(order)
        return out

    def _order_needs_handoff(self, order: Dict[str, Any]) -> bool:
        status = order.get("status", "")
        if status in ("אבוד", "באיחור"):
            return True
        eta = order.get("eta")
        if eta and status not in ("מוכן לאיסוף", "נמסר"):
            try:
                if dt.date.fromisoformat(eta) < dt.date.today():
                    return True
            except ValueError:
                pass
        return False

    # ---------- branches ----------
    def list_branches(self, city: Optional[str] = None, limit: int = 8) -> List[Dict[str, Any]]:
        items = self._branches
        if city:
            c = _norm(city)
            items = [b for b in items if c in _norm(b.get("city", "")) or c in _norm(b.get("name", ""))]
        return items[:limit]

    # ---------- policies / faq ----------
    def get_policies(self, topic: Optional[str] = None) -> str:
        if not topic:
            return self._policies
        # return the markdown section whose heading contains the topic
        sections = self._policies.split("\n## ")
        t = _norm(topic)
        for sec in sections:
            if t in _norm(sec.split("\n", 1)[0]):
                return "## " + sec if not sec.startswith("#") else sec
        return self._policies

    # ---------- shaping ----------
    @staticmethod
    def _compact(p: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "sku": p.get("sku"), "name": p.get("name"),
            "price": p.get("price"), "on_sale": p.get("on_sale", False),
            "price_was": (p.get("variants") or [{}])[0].get("old_price") if p.get("on_sale") else None,
            "colors": p.get("colors", []), "sizes": p.get("sizes", []),
            "availability": p.get("availability"), "url": p.get("url"),
            "image": p.get("image"),      # real product photo (hotlinked) for visual cards
        }

    @staticmethod
    def _full(p: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "sku": p.get("sku"), "name": p.get("name"),
            "description": p.get("description"),
            "price": p.get("price"), "price_min": p.get("price_min"),
            "price_max": p.get("price_max"), "on_sale": p.get("on_sale", False),
            "currency": p.get("currency", "ILS"),
            "colors": p.get("colors", []), "sizes": p.get("sizes", []),
            "availability": p.get("availability"),
            "category": (p.get("category_path") or "").split("/")[-1],
            "url": p.get("url"), "image": p.get("image"),
        }

    def stats(self) -> Dict[str, Any]:
        prices = [p["price"] for p in self._catalog if isinstance(p.get("price"), (int, float))]
        return {"products": len(self._catalog), "branches": len(self._branches),
                "demo_orders": len(self._orders),
                "price_min": min(prices) if prices else None,
                "price_max": max(prices) if prices else None,
                "on_sale": sum(1 for p in self._catalog if p.get("on_sale"))}


def _digits(s: Optional[str]) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


# module-level singleton (snapshot loaded once)
store = StoreClient()
