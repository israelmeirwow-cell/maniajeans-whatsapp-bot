# WhatsApp Customer Service Bot (demo)

Hebrew WhatsApp customer-service bot for a fashion store. Knows the catalog, answers
about products / orders / FAQ, and hands off to a human for cancellations, refunds,
and discounts. See [CLAUDE.md](CLAUDE.md) for the full design and the 7-intent spec.

> **Demo data is synthetic.** This public repo ships a small fictional catalog under
> `data/store/*.sample.*` (12 "DEMO" products, 3 fake branches, generic policies) so it
> runs out of the box. The app reads `data/store/<name>.json` if present and falls back to
> the `*.sample.*` twin otherwise. Swap in your own catalog — or, in production, point
> `app/woocommerce/client.py` at a live WooCommerce store — via the same interface.

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...          # or put it in .env
python3 -m uvicorn app.main:app --port 8123  # → http://localhost:8123 (chat) + /dashboard
python3 -m pytest tests/ -q                  # 43 tests
```

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# API key: put ANTHROPIC_API_KEY in .env (or it falls back to ../../.env)

# 1) Terminal chat (no WhatsApp needed):
python -m app.cli

# 2) HTTP API + web chat UI:
uvicorn app.main:app --reload
#   GET  /            WhatsApp-style demo chat UI
#   GET  /dashboard   business dashboard
#   GET  /health
#   POST /chat        {"session_id": "demo", "message": "יש לכם ג'ינס סלים?"}
```

## Go live on WhatsApp (Evolution API)

```bash
# 1) Start the gateway (Docker: Evolution API v2 + Postgres + Redis)
cd deploy/evolution && cp .env.example .env   # set AUTHENTICATION_API_KEY
docker compose up -d                          # API on :8080

# 2) Point the bot at it — in the project .env:
#    WA_GATEWAY_URL=http://localhost:8080
#    WA_GATEWAY_API_KEY=<same AUTHENTICATION_API_KEY>
#    WA_INSTANCE_NAME=maniajeans
#    WA_WEBHOOK_SECRET=<random string>
#    HANDOFF_NOTIFY=9725XXXXXXXX              # human agent's WhatsApp for escalations

# 3) Run the bot server, then pair the dedicated phone (QR) + register the webhook:
uvicorn app.main:app --port 8123
python3 -m app.whatsapp.setup                 # create instance → scan QR → webhook

# Tests (webhook parsing, gateway, end-to-end pipeline):
python3 -m pytest tests/ -q
```

⚠️ Phase 1 runs over Baileys (unofficial) — use a **dedicated number**, never the
business's main line. Phase 2 (official Cloud API) is a config change on the same
Evolution gateway.

## Layout
- `app/brain/` — intent classifier, prompts/persona, tools, agent loop
- `app/woocommerce/client.py` — store data (reads local snapshot; swap for live WooCommerce)
- `app/whatsapp/` — Evolution API gateway + webhook parsing (M1)
- `app/memory/` — per-customer session context
- `app/handoff/` — human escalation + structured summary
- `app/analytics/` — conversation logging & intent tagging
- `data/store/` — the demo catalog, branches, policies, demo orders
