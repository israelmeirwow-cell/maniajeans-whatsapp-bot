"""Customer-simulator loop — a Haiku 'customer' talks to the REAL bot, continuously, with a
delay between messages and a USD budget ceiling that auto-stops. Traffic flows through the
normal pipeline, so it lands on the dashboard and produces satisfaction/lessons/tickets.

Run:  python -m app.simulator --budget 0.50 --delay 20 --turns-per-convo 4
"""
import time
import json
import random
import argparse

from app.config import settings, DATA_DIR
from app.analytics import costs
from app.brain.agent import handle_message, close_conversation
from app.memory.conversations import store as conv_store
from app.simulator import personas as P
from app.simulator.customer import next_message, GREETING

_STATUS = DATA_DIR / "logs" / "sim_status.json"


def _write_status(running, budget, convo, sent, handoffs, intents_seen):
    try:
        _STATUS.parent.mkdir(parents=True, exist_ok=True)
        _STATUS.write_text(json.dumps({
            "running": running, "budget": budget,
            "cost_usd": round(costs.total_usd(), 4),
            "conversations": convo, "messages": sent, "handoffs": handoffs,
            "intents": intents_seen,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "by_model": costs.summary()["by_model"],
        }, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _client():
    import anthropic
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


def run(budget=0.50, delay=20.0, turns_per_convo=4, max_messages=100, persona_name=None):
    if not settings.has_claude:
        print("אין חיבור ל-Claude (ANTHROPIC_API_KEY). הסימולטור דורש LLM.")
        return
    client = _client()
    pinned = P.get(persona_name) if persona_name else None
    order = list(range(len(P.PERSONAS)))
    random.shuffle(order)

    print(f"🤖 סימולטור לקוחות — תקציב ${budget}, השהיה {delay}s, {turns_per_convo} תורים/שיחה")
    print("   עוצר אוטומטית בתקרת התקציב. Ctrl+C לעצירה ידנית.\n")

    sent = 0
    convo = 0
    intents_seen = {}
    handoffs = 0
    errors = 0
    try:
        while costs.total_usd() < budget and sent < max_messages:
            persona = pinned or P.PERSONAS[order[convo % len(order)]]
            sid = f"sim-{convo}-{random.randint(1000, 9999)}"
            history = [("bot", GREETING)]
            print(f"\n───── שיחה #{convo + 1} · פרסונה: {persona['name']} ─────", flush=True)

            try:
                for _ in range(turns_per_convo):
                    cust = next_message(persona, history, client)
                    if not cust:
                        break
                    history.append(("customer", cust))
                    print(f"👤 {cust}", flush=True)

                    res = handle_message(sid, cust)
                    history.append(("bot", res["reply"]))
                    sent += 1
                    intents_seen[res["intent"]] = intents_seen.get(res["intent"], 0) + 1
                    if res.get("handoff"):
                        handoffs += 1
                    flag = " → נציג" if res.get("handoff") else ""
                    mood = res.get("sentiment", "calm")
                    mtag = f" · {mood}" if mood != "calm" else ""
                    print(f"🤖 {res['reply']}", flush=True)
                    print(f"   [{res['intent']}{flag}{mtag}]  💰 ${costs.total_usd():.4f} / ${budget}",
                          flush=True)
                    _write_status(True, budget, convo, sent, handoffs, intents_seen)

                    if costs.total_usd() >= budget or sent >= max_messages:
                        break
                    time.sleep(delay)

                close_conversation(sid)     # satisfaction → lesson (feeds the learning loop)
            except Exception as e:
                # a transient API/network error must not kill a long unattended run
                errors += 1
                print(f"⚠️ שגיאה בשיחה ({type(e).__name__}: {str(e)[:100]}) — ממשיכים לשיחה הבאה",
                      flush=True)
                time.sleep(max(delay, 30))   # brief backoff
                if errors >= 10:
                    print("⛔ יותר מדי שגיאות רצופות — עוצר.", flush=True)
                    break
            finally:
                conv_store.reset(sid)
            convo += 1
    except KeyboardInterrupt:
        print("\n\n(עצירה ידנית)")

    _write_status(False, budget, convo, sent, handoffs, intents_seen)
    _report(convo, sent, intents_seen, handoffs, budget)


def _report(convo, sent, intents_seen, handoffs, budget):
    s = costs.summary()
    print("\n══════════ סיכום ריצה ══════════")
    print(f"שיחות: {convo} · הודעות: {sent} · העברות לנציג: {handoffs}")
    if intents_seen:
        print("כוונות שנבדקו: " + ", ".join(f"{k}={v}" for k, v in
                                            sorted(intents_seen.items(), key=lambda x: -x[1])))
    print(f"עלות מוערכת: ${s['total_usd']}  ({s['calls']} קריאות API)")
    for m, u in s["by_model"].items():
        print(f"   {m}: ${u['usd']}  ({u['calls']} קריאות, {u['input']}+{u['output']} טוקנים)")
    if s["total_usd"] >= budget:
        print("⛔ נעצר בתקרת התקציב.")


def main():
    ap = argparse.ArgumentParser(description="Mania Jeans customer simulator")
    ap.add_argument("--budget", type=float, default=0.50, help="USD ceiling; auto-stop")
    ap.add_argument("--delay", type=float, default=20.0, help="seconds between messages")
    ap.add_argument("--turns-per-convo", type=int, default=4)
    ap.add_argument("--max-messages", type=int, default=100, help="hard backstop")
    ap.add_argument("--persona", type=str, default=None, help="pin one persona by name")
    a = ap.parse_args()
    run(budget=a.budget, delay=a.delay, turns_per_convo=a.turns_per_convo,
        max_messages=a.max_messages, persona_name=a.persona)


if __name__ == "__main__":
    main()
