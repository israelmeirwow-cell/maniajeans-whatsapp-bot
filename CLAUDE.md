# CLAUDE.md — WhatsApp Customer Service Bot

> Project instructions for Claude Code. Read this before working in this repo.

## 1. What we are building

A WhatsApp chatbot that gives companies **automated customer service and sales support**.
The bot knows the company's full product catalog — what each product is, what it does,
how it works, pricing, stock, and shipping — and answers customer questions on WhatsApp
in a helpful, human, on-brand way. When it can't help, it hands off to a human agent.

**Primary market:** Israeli businesses. The bot talks to customers in **Hebrew by default**,
and mirrors the customer's language if they write in another language.

**North star:** a customer messages the business's WhatsApp number and gets an accurate,
fast, friendly answer 24/7 — as if a knowledgeable sales/support rep replied.

## 2. Architecture (the big picture)

Three layers, deliberately decoupled so the WhatsApp connection can be swapped without
touching the brain:

```
  Customer (WhatsApp)
        │
        ▼
┌─────────────────────┐   webhook (incoming msg)   ┌──────────────────────────┐
│  WhatsApp Gateway   │ ─────────────────────────► │   Python Backend (brain) │
│  (Evolution API)    │                            │   FastAPI                │
│  - Phase 1: Baileys │ ◄───────────────────────── │   - classify intent      │
│    (unofficial QR)  │   REST (send reply)        │   - orchestrate Claude   │
│  - Phase 2: Cloud   │                            │   - query WooCommerce    │
│    API (official)   │                            │   - session + handoff    │
└─────────────────────┘                            └────────────┬─────────────┘
                                                                │
                        ┌───────────────────┬───────────────────┼──────────────────┐
                        ▼                   ▼                   ▼                  ▼
                 ┌────────────┐     ┌─────────────────┐  ┌──────────────┐  ┌──────────────┐
                 │  Claude    │     │  WooCommerce    │  │  Conversation│  │  Logging &   │
                 │ (Anthropic)│     │ Store REST API  │  │  memory (DB) │  │  tagging     │
                 └────────────┘     │  (READ-ONLY)    │  └──────────────┘  └──────────────┘
                                    └─────────────────┘
```

**Why a gateway layer:** the mature "connect to a regular WhatsApp number" libraries
(whatsapp-web.js, Baileys) are Node.js, and we build the brain in Python. Evolution API
wraps them behind a clean REST + webhook interface, and the *same* interface also supports
the official WhatsApp Business Cloud API. So the Python code talks to one stable contract,
and the Phase 1 → Phase 2 migration becomes a config change, not a rewrite.

## 3. Tech stack

| Concern            | Choice                          | Notes |
|--------------------|---------------------------------|-------|
| Backend language   | **Python 3.11+**                | Per project decision |
| Web framework      | **FastAPI** + Uvicorn           | Async, great for webhooks |
| LLM                | **Claude** (Anthropic SDK)      | See model policy below |
| WhatsApp gateway   | **Evolution API** (self-hosted) | Baileys now → Cloud API later |
| Store data         | **WooCommerce REST API**        | **Read-only** — products, stock, orders |
| Conversation state | **SQLite** (dev) → Postgres/Redis (prod) | Per-customer session |
| Config             | `.env` via `pydantic-settings`  | Never hardcode secrets |

### Model policy
- **Intent classification:** `claude-haiku-4-5-20251001` — fast/cheap routing into the 7 intents.
- **Default reply workhorse:** `claude-sonnet-5` — best quality/cost balance for support answers.
- **Hard cases:** `claude-opus-4-8`.
- Use **tool calling** so the model queries live store data instead of guessing. Never let the
  model invent prices, stock, or order details — it must call a tool.

## 4. WhatsApp connection strategy

### Phase 1 (now) — unofficial, via Evolution API in Baileys mode
- Connects to a **regular WhatsApp number** by scanning a QR code.
- **⚠️ Important caveats — read before shipping to a real client:**
  - This is **against WhatsApp's Terms of Service**. There is a real risk the number gets banned.
  - **Use a dedicated number**, never the client's primary business line.
  - Warm the number up; don't blast messages; respect rate limits.
  - Treat Phase 1 as a **pilot / proof-of-concept**, not the long-term production channel.

### Phase 2 (future) — official WhatsApp Business Cloud API
- Requires: Meta Business verification, a WhatsApp Business Account, an approved phone number.
- **Costs money** (per-conversation pricing) but is reliable, allowed, and scalable.
- Design for these rules now so the switch is painless:
  - **24-hour customer service window:** you can freely reply within 24h of the customer's
    last message. Outside it, you must send a pre-approved **template message**.
  - Supports multiple agents + bot on one number.
- Because both modes sit behind Evolution API, Phase 2 is mostly: provision Cloud API creds,
  point Evolution at them, keep the same webhook/REST contract.

**Rule for Claude Code:** always send/receive WhatsApp through `app/whatsapp/gateway.py`.
Never call Baileys/Cloud API directly from business logic — keep the abstraction intact.

## 5. WooCommerce integration

> **Demo data source (current):** For the demo bot, the product/store data is a **static local
> snapshot** scraped from a real store (Mania Jeans, Magento) into `data/store/`:
> `catalog.json` (1,574 products — name, sku, description, category, price, sale, colors, sizes,
> per-variant prices, stock/availability), `branches.json` (56 branches with addresses, phones,
> weekly hours), `policies.md` + `catalog_summary.md`. The brain reads this snapshot exactly as
> it will later read WooCommerce, so swapping to a live WooCommerce store is a client-swap behind
> `app/woocommerce/client.py`, not a redesign. Nobody transacts against the demo.

- Auth via **REST API keys** (consumer key + secret), **READ-ONLY** — no write/update permissions
  back to the store, ever. The bot reads; it never mutates orders, stock, or prices.
- The bot reads: products, variations, prices, stock status, categories, and order status.
- **Real-time stock is mandatory:** stock/price/order-status shown to a customer must reflect the
  **live** store at the moment of the query — never stale/static data. Catalog *metadata*
  (names, descriptions, categories) may be cached and refreshed periodically for speed, but
  availability and order state are fetched live.
- All store access goes through `app/woocommerce/client.py`. The Claude agent reaches it only
  via defined tools (below), never raw.

### Agent tools (function calling surface)
- `search_products(query)` — find products by name/keyword/category.
- `get_product_details(id)` — description, price, attributes, dimensions, compatibility.
- `check_stock(id)` — **live** availability.
- `check_order_status(order_id | phone)` — live order tracking.
- `get_faq(topic)` — hours, contact, general shipping/returns policy from `data/policies.md`.
- `escalate_to_human(reason, summary)` — hand off to a human agent with context.

## 6. Bot behavior & persona

- **Tone:** warm, concise, professional, helpful — a great human sales/support rep.
- **Language:** Hebrew by default; mirror the customer's language.
- **Truth anchor:** business facts (prices, stock, policies, order details, branches) come
  **only** from tools — never invented. But a knowledge gap does **not** mean instant
  escalation: the bot follows the **response ladder** (§16) — verified answer → partial +
  clarify → clearly-framed general guidance → service ticket with a callback commitment →
  live agent only as a last resort.
- **Sound human, not scripted.** No `/ת` slash-gender in replies (reads robotic) — use the
  company plural voice ("מצטערים", "נשמח לעזור") or genderless infinitives ("אפשר לשלוח תמונה?").
  Vary wording every turn; no canned phrases. Refund/discount escalations go **through the LLM**
  (phrased fresh each time), not a fixed template — templates are the no-LLM fallback only.
- **No emojis** — the only one allowed is a single ❤️ as a warm closing at the very end
  (not 💔 or others, not mid-message), never per line.
- **Short WhatsApp-style messages**, not walls of text. Use the customer's name if known.
- **Don't re-ask** for details already given in the session (name, order number, etc.).
- **Sales-aware but not pushy:** answer first, then suggest a related/in-stock item when relevant.

## 7. Intent classification & handling (the 7 conversation types) — THE CORE SPEC

Every incoming message is first routed by a **Hebrew-tuned intent classifier** (Claude Haiku,
using the session context) into exactly one of these 7 intents. This table is authoritative —
it defines what the bot does on its own vs. what goes **immediately to a human**.

| # | Intent (HE / EN) | Owner | Behavior |
|---|------------------|-------|----------|
| 1 | **סטטוס הזמנה** / Order status | 🤖 Bot | Identify by **order number or phone**. Pull & present the **live** status (e.g. *בטיפול* / *הועבר לשילוח* / *מוכן לאיסוף*). **→ Handoff immediately** if the order **exceeds delivery SLA** or is **marked lost**. |
| 2 | **מידע על מוצרים** / Product info | 🤖 Bot | Answer technical questions (dimensions, compatibility, **live** stock availability) via **dynamic WooCommerce fetch**. |
| 3 | **שאלות נפוצות (FAQ)** | 🤖 Bot | Immediate answers on business hours, contact channels, and general shipping policy/times (from `data/policies.md`). |
| 4 | **ביטול הזמנה** / Order cancellation | 🤖 Bot (guarded action) | Self-service via the `cancel_order` action tool (§17): identity check (order+phone match), eligibility (only 'בטיפול'), explicit confirmation, audit. Failed verification ×2 / ineligible / no-LLM mode → human. |
| 5 | **זיכוי / החזר / מוצר פגום** / Refund / return / defective item | 🧑 Human | **Direct handoff.** Invite the customer to **attach photos/files** in the chat (esp. for defective products). |
| 6 | **בקשת הנחה / מחירי מבצע** / Discount or promo pricing | 🧑 Human | **ALWAYS** handoff. The bot is **not authorized** to set or change prices. |
| 7 | **פנייה כללית לא מזוהה** / Unrecognized intent | ⚠️→🧑 | Bot makes **exactly ONE** attempt to answer or redirect. If the intent is still unclear → **direct handoff**. |

**Handoff rule:** whenever an intent routes to a human (4, 5, 6, the escalation branches of 1
and 7), trigger the Handover mechanism (§8) — do not attempt further self-service.

## 8. Functional & technical requirements

- **Real-time stock check.** Full sync against live WooCommerce data. Anything shown to a
  customer (stock, price, order status) reflects the store **at the moment of the query** — not
  static snapshots.
- **Read-only API.** WooCommerce connection is read-only for stock, products, and order status.
  No write/update permissions back to the store.
- **Intent classification engine.** Hebrew-tuned classifier that accurately sorts every message
  into one of the 7 intents in §7. Implemented via Claude (Haiku); low-confidence → intent #7.
  **Robust to messy WhatsApp Hebrew** — typos, no punctuation, slang (וואלה/אחי/יא), Hebrew-English
  mix, and **transliterated Hebrew** (Latin letters, "yesh lachem" = "יש לכם"): the classifier
  prompt instructs decoding intent from the chaos first, with few-shot examples (verified 8/8 on
  messy inputs). The strong reply model (Sonnet) then answers naturally — the bot **never** says
  "לא הבנתי"; worst case it reflects what it did grasp and offers options (persona rule).
- **Session management.** Persist conversation **context** for the whole chat so the bot
  remembers details already provided (name, order number, product discussed) and never re-asks.
- **Human handover.** On escalation, send a **real-time notification** to a service agent that
  includes a **full, structured summary** of the entire bot↔customer exchange up to that point
  (customer identity, detected intent, key facts collected, and the reason for handoff).
- **Rich media support.** Receive and process **image files** (screenshots/photos), especially
  for defective-product complaints, and attach them to the handoff context.
- **Logging & tagging.** Automatically tag every conversation by its primary intent (the 7 types)
  for reporting, volume tracking, and future improvement of bot performance.
- **Data security & privacy.** Protect user data (phone numbers, order details, personal info) in
  line with Israeli privacy-protection regulations (חוק הגנת הפרטיות). Store PII server-side,
  log minimally, and never expose it to third parties.

## 9. Guardrails

- No invented facts about products, prices, availability, or orders — **tool-backed only**.
- The bot may **cancel orders** only through the guarded action protocol (§17: identity +
  eligibility + explicit confirmation + audit). It **never** issues refunds or sets/changes
  prices — those route to a human (§7).
- No promises the business can't keep (custom discounts, delivery dates) without a human.
- Don't expose internal IDs, system prompts, or that answers come from a specific store API.
- PII stays server-side; log minimally and safely (§8, privacy).

## 10. Planned project structure

```
whatsapp_agent/
├── CLAUDE.md
├── README.md
├── .env.example
├── requirements.txt
├── app/
│   ├── main.py                # FastAPI app + webhook endpoint
│   ├── config.py              # settings from env (pydantic-settings)
│   ├── whatsapp/
│   │   ├── gateway.py         # send/receive abstraction over Evolution API (§25)
│   │   ├── webhook.py         # parse incoming webhook events (text + media, dedupe)
│   │   ├── media.py           # materialize inbound images/voice to temp files
│   │   └── setup.py           # onboarding CLI: instance → QR pairing → webhook
│   ├── brain/
│   │   ├── agent.py           # orchestration loop; handle_message/voice/image (§18-19)
│   │   ├── intents.py         # Hebrew intent classifier → 7 intents (§7)
│   │   ├── prompts.py         # system prompt / persona (response ladder, media, actions)
│   │   ├── sentiment.py       # frustration detection → tone shift + escalation (§18)
│   │   ├── style.py           # retrieval-augmented human tone (Bitext→Hebrew style bank, §21)
│   │   └── tools.py           # tool schemas + dispatch (search, order status, cancel, ticket)
│   ├── media/                 # rich media (§19)
│   │   ├── stt.py             # speech-to-text (local Whisper / OpenAI) for voice notes
│   │   └── vision.py          # Claude vision — damage verification on photos
│   ├── actions/               # active bot — real operations (§17)
│   │   ├── orders.py          # guarded order cancellation (verify/eligibility/confirm)
│   │   └── audit.py           # append-only action audit trail
│   ├── woocommerce/
│   │   └── client.py          # store data (local snapshot; → live WooCommerce read-only)
│   ├── memory/
│   │   ├── conversations.py   # short-term: per-session context (within a chat)
│   │   └── customers.py       # long-term: profile + history ACROSS chats (§18)
│   ├── handoff/
│   │   ├── escalation.py      # routing + real-time agent notification
│   │   └── summary.py         # structured conversation summary for handover
│   ├── static/
│   │   ├── chat.html          # WhatsApp-style demo chat UI (served at /)
│   │   └── dashboard.html     # business dashboard (served at /dashboard)
│   ├── analytics/
│   │   ├── tagging.py         # auto-tag + log conversations by intent
│   │   ├── dashboard.py       # aggregations for /api/dashboard
│   │   └── costs.py           # API token-usage → USD tracker (§20)
│   ├── simulator/             # customer-simulator agent (§20)
│   │   ├── personas.py        # 10 customer personas (all intents + edge cases)
│   │   ├── customer.py        # Haiku plays the customer (dynamic, cheap)
│   │   └── run.py             # continuous loop w/ delay + budget ceiling
│   └── learning/
│       ├── feedback.py        # satisfaction assessment (implicit/explicit) + report
│       ├── lessons.py         # lessons memory: distill, store, retrieve, inject (§15)
│       └── embeddings.py      # pluggable embeddings (Voyage/OpenAI/local) for semantic retrieval
└── data/
    ├── store/                 # catalog.json, branches.json, policies.md, demo_orders.json
    ├── memory/customers.json  # long-term per-customer profiles (§18)
    ├── learning/              # lessons.jsonl, feedback.jsonl (the bot's growing memory)
    └── logs/                  # conversations, handoffs, tickets, actions (jsonl)
```

## 11. Environment variables (`.env`)

```
# Claude
ANTHROPIC_API_KEY=
CLAUDE_MODEL=claude-sonnet-5
CLAUDE_CLASSIFIER_MODEL=claude-haiku-4-5-20251001

# WhatsApp gateway (Evolution API)
WA_GATEWAY_URL=
WA_GATEWAY_API_KEY=
WA_INSTANCE_NAME=
WA_WEBHOOK_SECRET=

# WooCommerce (READ-ONLY keys)
WOO_STORE_URL=
WOO_CONSUMER_KEY=
WOO_CONSUMER_SECRET=

# Business rules
DELIVERY_SLA_DAYS=            # order older than this + not delivered => handoff (intent 1)

# App
DATABASE_URL=sqlite:///./conversations.db
HANDOFF_NOTIFY=              # phone/email/channel to alert a human agent
```

`.env` is git-ignored. Keep an up-to-date `.env.example` with keys but no values.

## 12. Development

> Commands are placeholders until scaffolded — update this section as we build.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload   # run the backend
# Evolution API runs separately (Docker); point its webhook at /webhook
```

## 13. Roadmap

- [x] **M0 — Scaffold:** FastAPI app, config, health check, project skeleton.
- [x] **M1 — WhatsApp live:** Evolution API layer complete (§25) — webhook in (text + image +
      voice), replies + product-photo cards out, QR onboarding CLI, handoff alerts. Needs only
      a running Evolution server (`deploy/evolution/`) + a dedicated number to go live.
- [x] **M2 — WooCommerce read:** read-only client + live `check_stock` / `check_order_status` /
      `search_products` / `get_product_details` (demo: static snapshot behind the same client).
- [x] **M3 — Intent engine:** Hebrew classifier routing to the 7 intents (§7) + handoff rules.
- [x] **M4 — The brain:** Claude agent with tool calling, Hebrew persona, grounded answers.
- [x] **M5 — Session memory:** per-customer context so the bot never re-asks known details.
- [x] **M6 — Handoff:** real-time agent notification + structured conversation summary + media.
- [x] **M7 — Logging & tagging:** auto-tag conversations by intent; basic volume reporting.
- [ ] **M8 — Pilot:** run on a dedicated number with a real (small) catalog; measure quality.
- [ ] **M9 — Official API:** migrate the gateway to WhatsApp Business Cloud API + templates.

## 14. Key decisions log

- **Python** backend (over Node) — user preference; gateway layer bridges the Node WhatsApp libs.
- **Evolution API** as the WhatsApp abstraction — supports unofficial (now) and official (later)
  behind one contract, making the Phase 1 → Phase 2 migration low-cost.
- **WooCommerce, read-only** as the product source of truth — bot queries live, never guesses,
  never writes back.
- **7 fixed intents** with a strict bot-vs-human routing table (§7) — cancellations, refunds,
  and discounts always go to a human; the bot never cancels or changes prices.
- **Start unofficial, migrate to official** — pilot fast now; go compliant + scalable later.

## 15. Continual learning loop (learn from mistakes)

The bot improves over time via an **evolving retrieved memory — not model retraining**
(Claude's weights are frozen; you cannot fine-tune it live). The loop:

1. **Capture satisfaction** (`app/learning/feedback.py`) — when a conversation ends,
   infer whether the customer was helped (Haiku reads the transcript → satisfied /
   neutral / dissatisfied), or take an explicit 👍/👎. Logged to `data/learning/feedback.jsonl`.
2. **Distill a lesson** (`app/learning/lessons.py`) — from a dissatisfied conversation,
   a knowledge gap, or a **human agent's correction**, produce a compact lesson
   `{intent, trigger, guidance, keywords}`. **Rule:** when the correction contains a
   concrete fact (warranty terms, a policy), the lesson stores the *fact itself* for
   reuse — not a vague "go check". Appended to `data/learning/lessons.jsonl`.
3. **Retrieve & apply** — on every new message, `lessons.retrieve(intent, text)` pulls
   the top relevant lessons and injects them into the system prompt. The bot now answers
   correctly on situations it previously failed — even for facts absent from catalog/policies.
   Retrieval is **semantic** via `embeddings.py` (a pluggable embedder: Voyage → OpenAI →
   local `sentence-transformers`, first available wins; falls back to keyword if none).
   A **precision-first gate** injects a lesson only on a *strong* semantic match, or a
   *moderate* one corroborated by a keyword overlap — so a wrong lesson is never injected
   (a false lesson could mislead the bot; a missed one just falls back to normal answering).
4. **Review** (`feedback.report()` / `GET /learning/report`) — satisfaction rates per
   intent surface recurring failure patterns for a human to fold into `policies.md` /
   the persona.

Entry points: `agent.close_conversation(session_id, explicit=)`, `agent.record_correction(
session_id, correction)`; HTTP `POST /close`, `POST /correct`, `GET /learning/report`.

## 16. Enterprise response ladder & service tickets

Modeled on how large-company service bots (banks/telecom) handle questions — graduated
response, progressive fallback, never a dead end, live agent as last resort:

1. **Verified answer from tools** → answer fully.
2. **Partial/adjacent verified info** → give what's known + one short clarifying question.
3. **General (non-business-specific) question** (how to measure size, jeans care) → answer
   from general knowledge, explicitly framed ("כהמלצה כללית..."); never embed an invented
   business fact inside it.
4. **Business-specific gap** (undocumented policy, edge case) → **do not guess, do not
   escalate**: open a ticket via the `create_service_ticket` tool — captures topic, exact
   question, name/phone — and commit: "פתחתי בירור, נחזור אליך עם תשובה מדויקת."
5. **Live agent (escalate_to_human)** only for: the always-human intents (§7), late/lost
   orders, explicit request for a human, anger/frustration, or two failed answer attempts.

**Every reply ends with a next step** (follow-up question / offer / commitment) — no dead ends.

## 17. Active bot — real actions with a safety protocol

The bot can now **perform operations**, not just answer (`app/actions/`). First action:
**order cancellation** (`cancel_order` tool → `actions/orders.py`). Safety layers, all enforced
**in code** (not just prompt):

1. **Identity verification** — supplied phone must match the phone on the order.
2. **Eligibility** — only orders still in `בטיפול` are auto-cancelable; shipped/ready/late/lost → human.
3. **Two-step confirmation** — call #1 (no `confirm`) only stages and returns a summary; the bot
   must present it and get an explicit "כן" before calling again with `confirm=true`.
4. **Blast radius** — max one executed cancellation per conversation.
5. **Audit trail** (`actions/audit.py` → `data/logs/actions.jsonl`) — every attempt recorded
   (staged/executed/refused_*), shown on the dashboard ("פעולות שהבוט ביצע") + KPI.

DEMO writes to `data/store/demo_orders.json`; in production this maps to the WooCommerce
orders API with a **narrowly-scoped write credential** (order status only — the catalog stays
read-only). Fallback (no-LLM) mode never executes actions — cancel → human.
Verified live: full verify→confirm→execute flow persisted 'בוטל'; wrong phone refused;
shipped order refused → human.

## 18. Human-grade conversation: memory, sentiment, upsell

Assembled into the system prompt **per turn** (`agent.handle_message` builds `extra_system`
from lessons + tone + customer note):

- **Short-term memory** — the session transcript is replayed each turn, so "יש את זה בשחור?"
  / "ואם אקנה שניים?" resolve against the product discussed earlier. Facts (name, order#,
  phone) are captured once and never re-asked.
- **Long-term memory** (`memory/customers.py`, `data/memory/customers.json`) — a durable
  profile per customer keyed by id (**in WhatsApp the id == the phone number = stable**).
  `close_conversation` writes a one-line history entry + any `open_issue` (set when the chat
  ended in a handoff). On return, `context_note` is injected → the bot greets by memory:
  "לפני כמה זמן שאלת על הזמנה 100238 — זה הסתדר, או שצריך עזרה?" (verified live).
- **Sentiment** (`brain/sentiment.py`) — keyword+`!` detection each turn (0 API cost),
  `calm|annoyed|angry`. `annoyed` → apologetic, focused, **no upsell**; `angry` → apologetic
  formal tone **and escalate to a human with a summary** (so the customer doesn't repeat
  themselves). No caps-shout heuristic (English product codes like DORI aren't anger).
  Logged per message and surfaced on the dashboard feed (😠/😕).
- **Upsell** — after a product answer, **in calm mood only**, suggest one genuinely
  complementary item (jeans → belt), catalog-backed via `search_products`; drop it instantly
  if the customer isn't interested. Verified live: DORI jeans → matching-belt suggestion.

## 19. Rich media — voice & vision (multimodal)

WhatsApp is voice- and photo-heavy; a text-only bot stalls. `app/media/`:

- **Voice notes → text** (`media/stt.py`, `agent.handle_voice`) — transcribe with **local
  Whisper** (no API key; needs ffmpeg) or the OpenAI Whisper API if `OPENAI_API_KEY` is set.
  The transcript runs through the **normal pipeline** (intent, sentiment, memory, tools), so a
  customer explaining a problem by voice is understood like any text message. Endpoint: `POST /voice`.
  (Local `small` model on Hebrew is usable but imperfect — API/larger model for production.)
- **Photos → damage verification, GROUNDED in the catalog** (`media/vision.py`,
  `agent.handle_image`) — Claude first checks whether the photographed item is even a **type the
  store sells** (men's fashion: clothing, footwear, accessories), returning
  `{item, is_our_product: yes|no|unclear, damage_visible, damage_type, recommendation}`.
  - `is_our_product=no` (an appliance, a random object, a wrong photo) → the bot says it isn't one
    of our products, flags a possible wrong photo, and asks for the correct item — it does **not**
    opine on a foreign item as if it were ours. (Same truth-anchor rule as text — verified live:
    a device photo was correctly rejected instead of described as a legit product.)
  - `is_our_product=yes` + damage visible → acknowledge + escalate with the assessment attached.
  - `is_our_product=yes` + no/unclear damage → ask for a clearer photo, never approve blindly.
  Endpoint: `POST /image` (multipart, optional caption). Chat UI has a 📎 attach button (image/audio).
  **Robust ingestion:** every upload is normalized with Pillow (open → RGB → downscale long edge
  ≤1568px → re-encode JPEG) so size/format never break the Claude call; unreadable files (bad/
  non-image, and HEIC unless `pillow-heif` is installed) return a friendly "resend as JPG" reply.
  `/image` and `/voice` always return **200 JSON**, never a 500 (fixed the "שגיאה בעיבוד הקובץ" bug).

## 20. Customer-simulator agent + cost tracking

An auto-QA agent (`app/simulator/`) that role-plays customers against the **real** bot to exercise
and improve it, designed to be **API-budget-safe**:

- **Dynamic customer** (`customer.py`) — a cheap **Haiku** call plays one of 10 personas
  (`personas.py`: shopper, order-check, late-order, damaged/refund, discount, FAQ, slang/typo,
  transliterated-Hebrew, angry, indecisive) and reacts to the bot's replies with contextual
  follow-ups. Customer side ≈ fractions of an agora per turn.
- **Continuous loop with a budget ceiling** (`run.py`, `python -m app.simulator`) — each turn:
  Haiku customer → real `agent.handle_message` → print + running cost → `--delay` sleep → stop
  when `costs.total_usd() >= --budget`. Ends each conversation with `close_conversation`
  (satisfaction → lesson), so gaps the sim finds become tickets/lessons. Flags: `--budget`
  (default 0.50, auto-stop), `--delay` (20s), `--turns-per-convo` (4), `--max-messages` (100),
  `--persona`. The budget is a soft ceiling — it stops within one turn of crossing.
- **Cost tracker** (`app/analytics/costs.py`) — every Claude call site reports `resp.usage` via
  `costs.record_usage(model, usage)`; priced per model (Haiku $1/$5, Sonnet 5 $2/$10 intro).
  Surfaced as a **dashboard KPI** ("עלות API מוערכת"). Note: costs are in-memory per process — the
  dashboard shows the *server's* live traffic; the simulator prints its own run cost separately
  (it drives the bot in-process, but its file-based logs still appear on the dashboard).

Verified: `--budget 0.05 --delay 1` → Haiku customer asked a discount question, reacted to the
bot's sale list with a stock/quantity follow-up, cost tracked per model, loop auto-stopped at the
ceiling; dashboard cost KPI renders.

## 21. Human sales tone — the Bitext→Hebrew style bank

Goal: the bot talks like a real Israeli sales rep, never generic — and **mirrors the customer's
register** (slang→loose+warm, polite→polite, keywords→terse), like a human does.

- **Source:** the Bitext customer-support dataset (26,872 EN rows, 27 intents, language-variation
  tags). Not used for fine-tuning (English + generic + ungrounded = would hurt); instead its two
  real assets were distilled: the intent taxonomy and the **register taxonomy**
  (Basic/Interrogative/Negation/Polite/Colloquial/Keywords/Abbreviations/Typos).
- **ETL** (scratchpad `build_style_bank.py`, one-off, ~$0.18): mapped Bitext intents → 12 store
  situations (incl. sales-specific ones Bitext lacks: product inquiry, price/sales, upsell-close);
  for each situation × 8 registers, Sonnet generated a Hebrew pair — authentic customer line
  (seeded by real Bitext phrasings) + an exemplary human rep reply. Replies obey the persona (no
  gender slashes, no emojis but optional ❤️) and contain **facts only as placeholders**
  ({{מוצר}}, {{מחיר}}...) so the bank teaches TONE, never data. → `data/style/style_bank.jsonl`
  (96 pairs).
- **Runtime** (`app/brain/style.py`): embeds the bank's customer lines (same pluggable embedder
  as lessons), retrieves the 2 closest exemplars per incoming message, and injects them into the
  system prompt as "כך נשמע נציג אנושי — חקה סגנון, לא תוכן". Facts still come exclusively from
  tools (truth anchor unchanged).
- **Simulator enrichment:** `simulator/customer.py` now randomizes a Bitext register per message
  (STYLE_MODES), so auto-QA exercises all registers.

Verified live: the same product question in slang / polite / keywords-only got three
register-matched replies ("יש וואלה..." vs "יש לנו כן!... ❤️" vs terse list) — all grounded in
real catalog items and prices; a slang defect complaint got slang-warm empathy + photo request.

**Tickets close the learning loop** (`app/handoff/tickets.py`, log: `data/logs/tickets.jsonl`):
`GET /tickets` lists open gaps; `POST /tickets/{id}/resolve {answer}` marks it answered AND
feeds the answer into `record_correction` → a lesson → the next customer asking (even in
different words — semantic retrieval) gets an instant correct reply. Verified end-to-end:
bitcoin-payment gap → ticket → business answer → a "קריפטו" phrasing answered instantly.

**Embeddings note:** the default local model (`paraphrase-multilingual-MiniLM-L12-v2`) ranks
Hebrew correctly but its similarity scores are compressed, so pure zero-word-overlap colloquial
paraphrases sit at the gate's edge. For a clear quality jump on Hebrew, set `VOYAGE_API_KEY`
(+`pip install voyageai`) or `OPENAI_API_KEY` — the embedder switches automatically, no code
change. Tunables: `EMBEDDINGS_MODEL`, `EMBEDDINGS_STRONG_SIM` (0.42), `EMBEDDINGS_MIN_SIM` (0.30).

## 22. Visual product results (real photos, not a text list)

When the bot calls `search_products` / `get_product_details`, the matching catalog items are
surfaced to the UI as a **visual card gallery** (real product photo + name + price + sale badge +
link), instead of the bot listing them in text.
- **Flow:** `client._compact` now carries `image`; `agent._run_llm` collects tool-result products
  into a `products` list (a fresh search replaces the shown set, a details lookup adds to it) and
  returns it alongside the reply (`handle_message` → `{..., "products": [...]}`); the `/chat`,
  `/image`, `/voice` responses expose it. `_fallback` (no-LLM) returns cards for product_info too.
- **Prompt:** §"הצגת מוצרים" tells the bot the customer already sees photos, so it writes a short
  human intro + a narrowing question (גזרה/מידה/צבע/תקציב) instead of dumping names+prices.
- **UI** (`chat.html` `addProducts`): horizontal RTL card row; each card links to the product page.
- **Image proxy** (`GET /img?u=&fb=`, main.py): fetches the store photo server-side with a browser
  Referer + in-memory cache; **SSRF-guarded** to the store host. **Hardened so a card can never
  disappear** (fix 2026-07-15, user: "התמונות נעלמות"): tries `u`, then the fallback `fb` (the
  product's default image, passed when a color-swapped image is used), then a neutral SVG
  placeholder — **always HTTP 200**, never an error or a raw-URL redirect (a redirect to the bare
  CDN URL was hitting hotlink protection → `onerror` → the old `a.remove()` deleted the card). The
  frontend `onerror` now only hides the `<img>`, never removes the card; card carries
  `image_default` as the `fb`. 2 retries/URL. Cards load via `/img?u=<enc>&fb=<enc default>`.
- Verified live: "אני מחפש מכנסי גינס מה יש לך להציע" → short human reply + 6 real jeans photos
  (6/6 loaded), prices, מבצע badges, working links. WhatsApp M1 will send these as native media.
- **Color-aware search (2026-07-15 fix — user: "ביקשתי חולצה שחורה והראה לי לבנה"):** search
  ignored color entirely (matched name/category only) and the card showed the single default photo
  (often the first/white variant). Fix in `client.py`: `_wanted_colors()` parses Hebrew color
  adjective forms (שחור/שחורה/שחורים… + English) → canonical root; `search_products` **filters to
  items available in the requested color** (substring match against each product's `colors`, so
  "כחול" hits "כחול בהיר"), strips color words from name-scoring, and tags the result with
  `matched_color`. The card shows a "צבע: X" chip; the prompt tells the bot to say "כולן קיימות
  בשחור" and never present another color as the requested one.
- **Per-color photos:** the scrape kept one image/product, so black items still showed the default
  photo. `app/woocommerce/color_images.py` parses each product page's Magento `jsonConfig`
  (balanced-brace extractor → color-attr options → per-variant `images`) into
  `data/store/color_images.json` (`sku → {color: url}`); `search_products` swaps the card image to
  `image_for(sku, matched_color)` when known, and `_maybe_reload()` mtime-reloads the cache live.
  Built by `scratchpad/enrich_color_images.py` (background, resumable, shirts/multi-color first,
  network-only). Verified live: "חולצה שחורה" → black-colorway photos (`-C7-` variant) + "צבע: שחור"
  chips + the bot confirms black availability.

## 24. Answering the actual question — cross-turn product memory + grounding (2026-07-15)

User: "הוא לא נותן לי תשובות על מה ששאלתי". Three root causes, all fixed:
- **No cross-turn product memory.** `Session.anthropic_messages()` is text-only — the products/SKUs
  shown were never retained, so a follow-up ("how much is the *second* one?", "is *that* one in L?")
  had no SKU and the bot re-searched or invented an answer. Fix: `Session.last_products` stores the
  cards shown each turn; `agent._shown_products_block()` injects them (ordinal, sku, price, colors,
  sizes) into the next turn's system prompt with the rule to answer by sku, call `check_stock`, and
  NOT re-show the whole list. Verified: "השנייה" → correct sku 40104-11 + price, no card re-spam;
  "הראשונה במידה L?" now actually calls `check_stock(sku, size)`.
- **Ungrounded stock claims.** Bot said "yes, in stock in L/32" without checking, then contradicted
  itself ("want me to check?"). Fix: a hard rule in SYSTEM_PROMPT §עוגן-האמת — never assert a
  size/color is in stock without `check_stock`; may state which sizes/colors a model *comes in* from
  data, but definitive availability needs the tool; never "in stock" followed by "want me to check".
  Verified: impossible size 7XL / color ורוד → honest "no" from catalog data.
- **Emoji leaks + reflexive clarifying questions.** Model leaked 🙂😊🖤 despite the persona rule.
  Fix: deterministic `agent._sanitize()` strips every emoji except a single trailing ❤️ (applied to
  every reply). Prompt §כללי-שיחה now says answer directly when enough info is given; a clarifying
  question only when a necessary detail is missing, never as a default closer.

## 23. Weekly business report (auto-generated + scheduled)

`app/analytics/weekly_report.py` — aggregates the last 7 days from the jsonl logs (no LLM) then
Haiku writes an exec summary + improvement recommendations grounded ONLY in the numbers.
- **Metrics** (real): volume (convos/messages/by-intent), **closures** (self-served sessions +
  self-serve %, resolved tickets, executed actions, handoffs), **satisfaction** + **how many
  customers got upset** (annoyed/angry sentiment), handoff reasons, **money** (profit/loss), what
  it learned (lessons added) + **open knowledge gaps** (open tickets).
- **Money model:** API cost is *measured* (see cost persistence below); savings/net/sales-potential
  are *labeled estimates* from configurable assumptions (`COST_PER_HUMAN_CONTACT_ILS`=12,
  `USD_TO_ILS`=3.7, `ASSUMED_CONVERSION`=0.08, `AVG_ORDER_VALUE_ILS`=220). The report prints its
  assumptions — no invented revenue (no checkout integration yet).
- **Cost persistence:** `costs.record()` now also appends each call to `data/logs/usage.jsonl`;
  `costs.spend_since(ts)` sums real weekly spend across processes/restarts (in-memory total stays).
- **View/API:** `GET /report` (rendered RTL HTML, linked from the dashboard header) and
  `GET /report/weekly?days=7` (JSON); both cached 10 min in `main.py` so refresh doesn't re-bill.
- **Delivery:** `render_whatsapp` (condensed, phone-friendly) → `whatsapp()` via the Evolution
  gateway (no-ops gracefully until M1; owner number = `REPORT_WHATSAPP_TO`); `email()` via SMTP is
  also ready (`has_smtp`, Gmail App Password). `render_text`/`render_html` + `save()` write dated
  files to `data/reports/`.
- **Schedule:** `python -m app.analytics.weekly_report [--whatsapp|--email|--quiet]`. A **launchd
  job** (`deploy/com.maniajeans.weekly-report.plist` → `~/Library/LaunchAgents/`, runs
  `deploy/run_weekly_report.sh`) fires **every Sunday 09:00**, generating + saving + attempting
  WhatsApp delivery. Verified live: report reflects real sim traffic; AI correctly flagged the
  ~38% unrecognized rate as the top issue; launchd registered; WhatsApp path no-ops cleanly.

## 25. WhatsApp live connection — the Evolution API layer (M1, 2026-07-15)

Research first (per the build brief): studied how production WhatsApp bots are wired —
Evolution API v2 contract (instance lifecycle, `MESSAGES_UPSERT` webhook with `webhookBase64`,
`sendText`/`sendMedia`, `getBase64FromMediaMessage`), Baileys JID formats, webhook retry
behavior. The layer:

- **Inbound** (`whatsapp/webhook.py`): normalizes `MESSAGES_UPSERT` into `text` / `image` /
  `audio` events (caption, mimetype, inline base64, message key, pushName). Ignores own
  messages (`fromMe`), groups (`@g.us`), status/newsletter broadcasts, reactions/protocol
  events; unwraps ephemeral/viewOnce; **dedupes by message id** (Evolution retries webhooks —
  a customer is never answered twice; verified live: redelivery → `{"ignored": true}`).
- **Pipeline** (`main.py` `_process_whatsapp`): the webhook **acks immediately** and the brain
  runs as a background task (so Evolution never re-fires on slow LLM turns). Flow: typing
  presence → route text→`handle_message` / image→save+`handle_image` / voice→save+`handle_voice`
  → `sendText` reply → up to 3 product cards as **native photo messages** (store photo fetched
  server-side with Referer → base64 `sendMedia`, captions = name/price/מבצע/link). pushName
  seeds `facts["name"]` so the bot greets by name. Any pipeline error → friendly Hebrew
  apology, never silence.
- **Outbound** (`whatsapp/gateway.py`): `send_message`, `send_typing`, `send_image`,
  `send_product_cards`, `fetch_media_base64` (fallback when the webhook didn't inline media),
  `notify_agent` (real-time handoff alert to `HANDOFF_NOTIFY`'s WhatsApp — called from
  `escalation.escalate`, so web + WhatsApp both alert), instance lifecycle
  (`create_instance`/`connect_qr`/`connection_state`/`set_webhook`). Everything is a graceful
  no-op until `WA_GATEWAY_URL`/`WA_INSTANCE_NAME` are set — the web demo needs no Evolution.
- **Security:** webhook registered at `/webhook/{WA_WEBHOOK_SECRET}`; bare `/webhook` rejects
  when a secret is configured. Media lands in temp files, deleted after processing; Evolution
  compose disables message-body persistence (privacy, חוק הגנת הפרטיות).
- **Onboarding:** `deploy/evolution/docker-compose.yml` (Evolution v2 + Postgres + Redis) then
  `python3 -m app.whatsapp.setup` — creates the instance, saves+opens the pairing QR, waits for
  `state=open`, registers the webhook (`MESSAGES_UPSERT`, base64 on), optional send smoke-test.
- **Verified end-to-end** against a fake Evolution server + the real brain: order-status
  question → grounded reply (name/item/courier/tracking all from the order record); product
  search → text + 3 real photo cards; defective-item photo (base64 through the whole pipe) →
  vision said unclear → bot asked for a clearer photo (correct §19 behavior); refund demand →
  escalation + **real-time agent WhatsApp alert with the full structured summary**; duplicate
  delivery ignored. Tests: `tests/test_whatsapp.py` (18, all passing) — parsing shapes, media
  materialization, gateway payloads, secured-webhook auth, pipeline e2e, handoff notification.

## 26. Outfit intelligence + image-serving hardening (2026-07-15, user: "נתקע ולא מביא תמונות / ביקשתי לוק וקיבלתי רק נעליים")

- **"תרכיב לי לוק" showed only shoes.** Root cause: in `agent._run_llm`, every `search_products`
  call CLEARED the collected card set ("fresh search replaces"), so an outfit turn (shirt →
  pants → shoes searches) surfaced only the last search. Fix: per-search groups merged by
  `_merge_product_groups` — one search → its top 8; several searches → **top 3 of EACH group**
  (sku-deduped, cap 9), so every category is represented. Prompt gained §"הרכבת לוק": for a
  look/outfit request the bot MUST search each category separately, recommend one item per
  category, and explain why they work together. Verified live: 9 cards = 3 חולצות + 3 מכנסיים
  + 3 נעליים, coherent styling advice, browser 9/9 photos loaded.
- **Contextual intent classification.** "אוקיי עכשיו חולצה" (a follow-up) classified
  `unrecognized` because the classifier saw only the bare message. `intents.classify` now takes
  `context` = the last 4 session turns (passed by `handle_message`), with prompt guidance that a
  follow-up inherits the conversation's topic. Verified: same follow-up now → `product_info`,
  answered by sku from the shown set.
- **/img proxy hardening** (the "server stuck, no images" complaint): endpoint is now **async**
  (shared `httpx.AsyncClient`, semaphore 8 — image bursts can't occupy worker threads), with a
  **disk cache** (`data/cache/img/`, survives restarts; repeat load of 9 images: 0.1s) and a
  **negative cache** (a dead URL fails once, ≤6s, then serves the fallback/placeholder instantly
  for 10 min). Sync `_fetch_img` (used by WhatsApp product cards) shares the same caches.
  Fallback chain unchanged: u → fb → SVG placeholder, always HTTP 200.
- **No raw Markdown in replies:** the model leaked `**bold**`/`##` (renders as literal asterisks
  in WhatsApp/our UI). `_sanitize` now converts `**x**` → WhatsApp-native `*x*` and strips
  heading markers; prompt forbids Markdown explicitly. Tests: `tests/test_look_and_images.py`
  (7) — merge logic, disk+negative cache, SSRF placeholder, sanitize. Suite: 25 passing.
- **Search ranking: the product TYPE now dominates** (the deeper "no shoes" root cause — found
  live even after the merge fix): scoring counted matching tokens equally, so "נעליים קלאסיות"
  ranked a "עניבה קלאסית" / "חגורת עור קלאסית" (adjective hits) above every shoe, and the bot
  told the customer the store has no shoes. Fix in `client.search_products`: the query's FIRST
  token matching the product's name-head (first 2 words) scores 8, other tokens on the head 3,
  descriptive hits 1 — and when any head match exists (best ≥ 8), cross-category noise
  (score < 8) is dropped entirely. Prompt (§הרכבת לוק) also gained: a 0-result search is not
  proof of absence — retry with the bare category word before saying "אין לנו".
  Tests: `tests/test_search.py` (7 relevance regressions). Suite: 32 passing.

## 27. Skills architecture + prompt caching + load control (2026-07-15, "תחלק את האפיון למוח וסקילים, תוריד עומסים")

Researched production CS-bot patterns (intent routing → per-intent playbooks; lean fixed
context + on-demand detail; prompt caching as the main cost lever). Implemented:

- **Skills prompting** (`app/brain/skills.py`) — the monolithic SYSTEM_PROMPT was split into
  **CORE** (persona, truth anchor, condensed response ladder, conversation rules, tools index —
  sent every turn) plus **7 per-intent playbooks** (§7's conversation types: order_status,
  product_info incl. cards/color/outfit/upsell, faq, cancel_order protocol, refund_return,
  discount [sale-info vs haggling], unrecognized [one attempt]). `handle_message` loads CORE +
  only the playbook for the classified intent — the model reads one focused playbook per turn,
  not all seven. `prompts.py` keeps only HANDOFF_MESSAGES (SYSTEM_PROMPT kept as reference).
- **Prompt caching** (`skills.system_blocks` + `_run_llm`) — 3 breakpoints: (1) after CORE
  (caches tool schemas + persona, stable across ALL turns), (2) after the playbook (stable per
  intent), (3) on the newest history message (append-only conversation → incremental reuse).
  Volatile per-turn context (lessons/style/tone/memory/shown-products) sits after the last
  breakpoint. **Measured live: turn-1 wrote 5,395 tokens; every later call read 5,000-6,270
  from cache with only 2-200 uncached input tokens — a 2-turn conversation cost $0.0074 vs
  ~$0.027 uncached (~73% cheaper), and TTFT drops with it.**
- **Cache-aware costs** (`analytics/costs.py`) — `record()` now tracks cache_read (0.1× input
  price) and cache_write (1.25×) per call, persisted to usage.jsonl; dashboard/weekly money
  stays honest.
- **Load control** (`memory/conversations.py`) — LLM receives only the last 30 turns
  (`anthropic_messages(max_turns)`, always starting on a user turn; full transcript kept for
  handoffs); sessions idle >12h evicted; store hard-capped at 500 (LRU) — memory can't grow
  unbounded at 1,000+ conversations/month.
- **Startup warmup** (`main.py` startup hook) — the sentence-transformers embedder + style/
  lesson matrices load in a background thread at boot, so the first customer message is no
  longer blocked for seconds by torch init.
- **Classifier fix** (found in live smoke): "תרכיב לי לוק" classified `unrecognized`, so the
  product playbook (with the multi-category outfit mandate) never loaded → look without shoes.
  Added styling examples to the Haiku classifier prompt + לוק/אאוטפיט/סטיילינג keywords to the
  fallback. Verified: intent=product_info, 9 cards = shirts+pants+shoes.
- Verified live across intents: order_status (grounded, greeted by order name), refund
  (empathy → photo request per playbook), product follow-up ("הראשונה במידה L?" → definitive
  check_stock answer). Tests: `tests/test_skills_and_load.py` (11) — playbook selection/focus,
  cache block layout & byte-stability, history cap, eviction, cache pricing. Suite: 43 passing.
