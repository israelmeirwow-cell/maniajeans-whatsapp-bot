"""Search-relevance regression tests — the product TYPE must dominate ranking.

Born from a real failure: "תרכיב לי לוק" searched "נעליים קלאסיות" and got a tie,
a belt and a shirt (adjective matches) ranked above every shoe — so the bot told
the customer the store has no shoes.

Run:  python3 -m pytest tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.woocommerce.client import store


def _names(query, **kw):
    return [p["name"] for p in store.search_products(query, **kw)]


def test_shoes_query_returns_only_shoes():
    for q in ("נעליים", "נעליים קלאסיות", "נעליים יומיומיות קלאסיות"):
        names = _names(q)
        assert names, f"no results for {q!r}"
        for n in names:
            assert n.startswith(("נעל", "סניקרס", "מגפ")), f"{q!r} returned non-shoe: {n}"


def test_shirt_query_returns_shirts():
    names = _names("חולצה מכופתרת")
    assert names and all("חולצ" in n for n in names)


def test_jeans_query_returns_jeans():
    names = _names("גינס בגזרה ישרה")
    assert names and all("גינס" in n or "ג'ינס" in n for n in names)


def test_belt_query_not_hijacked_by_adjective():
    names = _names("חגורה אלגנטית")
    assert names and all("חגור" in n for n in names)


def test_absent_type_returns_empty_not_noise():
    # the catalog has no coats — better an honest empty than random "מעיל-like" items
    assert _names("מעיל צמר ארוך") == []


def test_color_filter_still_works():
    res = store.search_products("חולצה שחורה")
    assert res and all(r.get("matched_color") for r in res)


def test_on_sale_and_price_filters_still_work():
    res = store.search_products("חולצה", on_sale=True, max_price=120)
    assert res and all(p["on_sale"] and p["price"] <= 120 for p in res)
