"""Tests for the outfit-composition fix (multi-search card merging) and the hardened
/img proxy (disk cache, negative cache, always-200)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.brain.agent import _merge_product_groups
import app.main as main


def _c(sku, name="x"):
    return {"sku": sku, "name": name}


def test_single_search_shows_up_to_eight():
    groups = [[_c(str(i)) for i in range(12)]]
    out = _merge_product_groups(groups, [])
    assert len(out) == 8
    assert out[0]["sku"] == "0"


def test_outfit_three_searches_all_categories_represented():
    """The 'תרכיב לי לוק' bug: shirt+pants+shoes searches must ALL surface —
    not only the last one."""
    shirts = [_c(f"s{i}", "חולצה") for i in range(6)]
    pants = [_c(f"p{i}", "מכנס") for i in range(6)]
    shoes = [_c(f"n{i}", "נעל") for i in range(6)]
    out = _merge_product_groups([shirts, pants, shoes], [])
    names = {c["name"] for c in out}
    assert names == {"חולצה", "מכנס", "נעל"}
    assert len(out) == 9                       # 3 per category
    assert [c["sku"] for c in out[:3]] == ["s0", "s1", "s2"]


def test_refine_search_dedupes_overlap():
    broad = [_c("a"), _c("b"), _c("c")]
    refined = [_c("b"), _c("c"), _c("d")]
    out = _merge_product_groups([broad, refined], [])
    assert [c["sku"] for c in out] == ["a", "b", "c", "d"]


def test_details_lookup_appends():
    out = _merge_product_groups([[_c("a")]], [_c("z"), _c("a")])
    assert [c["sku"] for c in out] == ["a", "z"]


# ---------------- /img proxy hardening ----------------

def test_img_disk_cache_and_negative_cache(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import httpx

    monkeypatch.setattr(main, "_IMG_CACHE_DIR", tmp_path)
    monkeypatch.setattr(main, "_img_neg", {})
    url = "https://www.maniajeans.co.il/pub/media/x.jpg"
    calls = {"n": 0}

    class FakeResp:
        status_code = 200
        headers = {"content-type": "image/jpeg"}
        content = b"JPEGBYTES"

    class FakeAsyncClient:
        def __init__(self, **kw):
            pass
        async def get(self, u):
            calls["n"] += 1
            return FakeResp()

    monkeypatch.setattr(main, "_img_aclient", None)
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    client = TestClient(main.app)
    r1 = client.get("/img", params={"u": url})
    assert r1.status_code == 200 and r1.content == b"JPEGBYTES"
    assert calls["n"] == 1
    r2 = client.get("/img", params={"u": url})          # disk-cache hit — no refetch
    assert r2.content == b"JPEGBYTES" and calls["n"] == 1

    # a dead URL: one failed attempt, then negative-cached → instant placeholder, no refetch
    class DeadClient(FakeAsyncClient):
        async def get(self, u):
            calls["n"] += 1
            raise httpx.ConnectError("down")

    monkeypatch.setattr(main, "_img_aclient", DeadClient())
    dead = "https://www.maniajeans.co.il/pub/media/dead.jpg"
    r3 = client.get("/img", params={"u": dead})
    assert r3.status_code == 200 and b"svg" in r3.content
    n_after_first = calls["n"]
    r4 = client.get("/img", params={"u": dead})
    assert r4.status_code == 200 and calls["n"] == n_after_first   # neg-cache: no second try


def test_img_foreign_host_rejected_with_placeholder():
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    r = client.get("/img", params={"u": "https://evil.example.com/x.jpg"})
    assert r.status_code == 200 and b"svg" in r.content


def test_sanitize_converts_markdown_to_whatsapp_bold():
    from app.brain.agent import _sanitize
    t = _sanitize("הרכבתי לוק:\n- **חולצה מכופתרת** בלבן\n## כותרת\nרגיל")
    assert "**" not in t
    assert "*חולצה מכופתרת*" in t
    assert "##" not in t
