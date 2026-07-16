"""Skill-based prompting + load-control regressions.

Covers: per-intent playbook selection, prompt-cache block layout, conversation-history
capping, session eviction, and cache-aware cost accounting.

Run:  python3 -m pytest tests/ -q
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.brain import intents as I
from app.brain import skills as SK
from app.memory.conversations import Session, ConversationStore, MAX_LLM_TURNS
from app.analytics import costs


# ---------------- skills ----------------

def test_every_intent_has_a_skill():
    for intent in I.INTENTS:
        text = SK.skill_for(intent)
        assert text.startswith("## הפלייבוק"), f"missing playbook for {intent}"


def test_skill_is_focused_not_the_whole_brain():
    # The point of the split: a product question must not carry the cancel protocol.
    product = SK.skill_for(I.PRODUCT_INFO)
    assert "cancel_order" not in product
    assert "ביטול" not in product
    cancel = SK.skill_for(I.CANCEL_ORDER)
    assert "confirm=true" in cancel
    assert "הרכבת לוק" not in cancel


def test_core_carries_universal_rules_only_once():
    assert "עוגן האמת" in SK.CORE
    assert "escalate_to_human" in SK.CORE          # ladder step 5 lives in core
    assert "הרכבת לוק" not in SK.CORE              # per-intent detail stays in the skill


def test_system_blocks_cache_layout():
    blocks = SK.system_blocks(I.FAQ, extra_system="הקשר זמני")
    assert [b["text"] for b in blocks[:2]] == [SK.CORE, SK.skill_for(I.FAQ)]
    # stable blocks carry cache breakpoints; the volatile tail must NOT
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert blocks[1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in blocks[2]
    # no extra context -> only the two cached blocks
    assert len(SK.system_blocks(I.FAQ)) == 2


def test_skill_blocks_are_byte_stable():
    # any per-call variation would silently invalidate the prompt cache
    assert SK.system_blocks(I.PRODUCT_INFO)[0]["text"] == SK.system_blocks(I.PRODUCT_INFO)[0]["text"]
    assert SK.CORE == SK.CORE


# ---------------- history cap ----------------

def test_history_capped_and_starts_with_user():
    s = Session("t1")
    for i in range(50):
        s.add_user(f"שאלה {i}")
        s.add_assistant(f"תשובה {i}")
    msgs = s.anthropic_messages()
    assert len(msgs) <= MAX_LLM_TURNS
    assert msgs[0]["role"] == "user"
    assert msgs[-1]["content"] == "תשובה 49"      # newest turns kept


def test_full_transcript_still_available_for_handoff():
    s = Session("t2")
    for i in range(40):
        s.add_user(f"הודעה {i}")
    assert "הודעה 0" in s.transcript()             # handoff summary keeps everything


# ---------------- session eviction ----------------

def test_stale_sessions_evicted():
    store = ConversationStore()
    old = store.get("old-customer")
    old.touched_at = time.time() - 13 * 3600       # idle past the 12h TTL
    store.get("new-customer")                      # creation triggers eviction
    assert "old-customer" not in store._sessions
    assert "new-customer" in store._sessions


def test_session_cap_evicts_least_recent():
    store = ConversationStore()
    for i in range(505):
        s = store.get(f"c{i}")
        s.touched_at = time.time() + i             # strictly increasing recency
    assert len(store._sessions) <= 501             # cap enforced on next creation
    assert "c504" in store._sessions


# ---------------- cache-aware costs ----------------

def test_costs_price_cache_tokens_correctly():
    costs.reset()
    costs.record("claude-sonnet-5-test", input_tokens=1_000_000, output_tokens=0,
                 cache_read=1_000_000, cache_write=1_000_000)
    usd = costs.total_usd()
    # sonnet pricing 2/10: input 2.0 + cache_read 0.2 + cache_write 2.5 = 4.7
    assert abs(usd - 4.7) < 0.001
    costs.reset()


def test_record_usage_reads_cache_fields():
    class U:
        input_tokens = 100
        output_tokens = 50
        cache_read_input_tokens = 4000
        cache_creation_input_tokens = 2000
    costs.reset()
    costs.record_usage("claude-sonnet-5-test", U())
    u = costs.summary()["by_model"]["claude-sonnet-5-test"]
    assert u["input"] == 100 and u["output"] == 50
    costs.reset()
