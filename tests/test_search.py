"""Search-relevance regression tests — the product TYPE must dominate ranking.

Born from a real failure: "תרכיב לי לוק" searched "נעליים קלאסיות" and got a tie,
a belt and a shirt (adjective matches) ranked above every shoe — so the bot told
the customer the store has no shoes.

These tests run against a small CONTROLLED fixture catalog defined here, not against
whichever catalog happens to be loaded (real snapshot on the owner's machine, synthetic
sample on a fresh clone). That makes them deterministic — they assert on the ranking
LOGIC and pass everywhere, regardless of the shipped data.

Run:  python3 -m pytest tests/ -q
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.config as cfg
from app.woocommerce.client import StoreClient


def _p(sku, name, category, colors, sizes, price, was=None):
    # On-sale items carry a variant with `old_price` so StoreClient._compact() can surface
    # price_was on the card — mirrors the real catalog shape (Greptile review, PR #1).
    variants = [{"old_price": was}] if was else []
    return {
        "url": f"https://example.com/{sku}", "category_path": f"בגדי גברים / {category}",
        "name": name, "sku": sku, "description": f"{name} — פריט בדיקה.",
        "image": "", "price": price, "currency": "ILS", "availability": "in_stock",
        "colors": colors, "sizes": sizes, "variants": variants,
        "price_min": price, "price_max": (was or price), "on_sale": bool(was),
    }


# A controlled catalog covering shoes / shirts / jeans / belt — and deliberately NO coat,
# so "absent type" queries have a real absent type to assert on. Adjectival items
# ("אלגנטית") exist so the "not hijacked by an adjective" test genuinely exercises the guard.
_FIXTURE = [
    _p("SHOE-1", "נעלי עור",                 "נעליים",       ["שחור"],        ["42", "43"], 259.9),
    _p("SHOE-2", "נעלי סניקרס",              "נעליים",       ["לבן", "שחור"], ["42", "43", "44"], 199.9),
    _p("SHOE-3", "סניקרס קלאסיות",           "נעליים",       ["לבן"],         ["41", "42"], 189.9),
    _p("SHIRT-1", "חולצה מכופתרת קלאסית",     "חולצות",       ["לבן", "שחור"], ["M", "L", "XL"], 99.9, was=149.9),
    _p("SHIRT-2", "חולצה מכופתרת אלגנטית",    "חולצות",       ["לבן"],         ["M", "L"], 129.9),
    _p("SHIRT-3", "חולצת טי בייסיק",          "חולצות",       ["שחור", "לבן"], ["S", "M", "L"], 49.9),
    _p("JEAN-1", "ג'ינס סקיני",              "מכנסי ג'ינס",  ["כחול", "שחור"], ["30", "32", "34"], 189.9),
    _p("JEAN-2", "ג'ינס בגזרה ישרה",         "מכנסי ג'ינס",  ["כחול"],        ["32", "34", "36"], 219.9),
    _p("BELT-1", "חגורה קלאסית מעור",         "אביזרים",      ["שחור"],        ["ONE"], 69.9),
]


@pytest.fixture(scope="module")
def store():
    """A StoreClient loaded over the controlled fixture catalog above."""
    # color_images.json is intentionally NOT written here: color_images.py binds its cache
    # path at import time (before this fixture runs), so a temp copy would never be read
    # (Greptile review, PR #1). image_for() returns None for these SKUs, which is fine.
    d = Path(tempfile.mkdtemp())
    (d / "catalog.json").write_text(json.dumps(_FIXTURE, ensure_ascii=False), encoding="utf-8")
    (d / "branches.json").write_text("[]", encoding="utf-8")
    (d / "demo_orders.json").write_text("[]", encoding="utf-8")
    (d / "policies.md").write_text("demo", encoding="utf-8")
    old = cfg.STORE_DIR
    cfg.STORE_DIR = d                       # store_file() reads this at load time
    try:
        client = StoreClient(store_dir=d)   # caches the fixture data in __init__
    finally:
        cfg.STORE_DIR = old                 # restore immediately; the client keeps its copy
    yield client
    shutil.rmtree(d, ignore_errors=True)


def _names(store, query, **kw):
    return [p["name"] for p in store.search_products(query, **kw)]


def test_shoes_query_returns_only_shoes(store):
    for q in ("נעליים", "נעליים קלאסיות", "נעליים יומיומיות קלאסיות"):
        names = _names(store, q)
        assert names, f"no results for {q!r}"
        for n in names:
            assert n.startswith(("נעל", "סניקרס", "מגפ")), f"{q!r} returned non-shoe: {n}"


def test_shirt_query_returns_shirts(store):
    names = _names(store, "חולצה מכופתרת")
    assert names and all("חולצ" in n for n in names)


def test_jeans_query_returns_jeans(store):
    names = _names(store, "גינס בגזרה ישרה")
    assert names and all("גינס" in n or "ג'ינס" in n for n in names)


def test_belt_query_not_hijacked_by_adjective(store):
    # "אלגנטית" also appears on a shirt; the TYPE word (חגורה) must dominate so only belts return
    names = _names(store, "חגורה אלגנטית")
    assert names and all("חגור" in n for n in names)


def test_absent_type_returns_empty_not_noise(store):
    # the fixture has no coats — better an honest empty than random "מעיל-like" items
    assert _names(store, "מעיל צמר ארוך") == []


def test_color_filter_still_works(store):
    res = store.search_products("חולצה שחורה")
    assert res and all(r.get("matched_color") for r in res)


def test_on_sale_and_price_filters_still_work(store):
    res = store.search_products("חולצה", on_sale=True, max_price=120)
    assert res and all(p["on_sale"] and p["price"] <= 120 for p in res)
    # on-sale cards must carry the original price (derived from the variant's old_price)
    assert all(p.get("price_was") and p["price_was"] > p["price"] for p in res)
