# Hebrew Voice & Tone — extracted from real inbox email (July 2026)

## Source & honest caveat
Extracted from a sample of the account owner's Gmail inbox (~50 of ~201 "primary" threads,
with full-body reads of the human-written Hebrew ones). **The inbox contained no real
customer-service conversations** — it is almost entirely automated/marketing/notification mail.
So this file captures **Israeli Hebrew email writing style/voice**, which is useful for the bot's
*tone*. It is **not** a substitute for real customer-support dialogue data — that will only
accumulate once the bot is live (or must be synthesized; see §"Next step").

## Openers (warm, first-name, casual)
Real examples seen:
- `היי ישראל,`
- `מה קורה ישראל,`
- `שלום ישראל מאיר,`

→ For the bot: greet with the customer's first name + a warm, casual Hebrew opener.

## Tone characteristics (observed)
- **Second person, direct:** "אתה מגדיר פעם אחת מה לעשות ומתי", "תשאלו, תקבלו תשובות".
- **Short, punchy sentences — one idea per line** (very WhatsApp-friendly).
- **Rhetorical questions to engage:** "למה אני בכלל צריך להיות שם?".
- **Gender-inclusive forms** when gender is unknown: `את/ה`, `ברוך/ה הבא/ה`, `דרוש/ה`.
- **Em-dash for emphasis / mini-lists:** "— שאלות ועזרה — תשאלו, תקבלו תשובות".
- **Everyday words, minimal jargon.** Warm and human, not corporate.
- **Emojis** appear mostly in subject lines (🔥 ⚡ 🛒 📋 🌙) — used sparingly in the body.

## Sign-offs (observed)
- `באהבה, [שם]`
- `נתראה בפנים, [שם]`
- `נ.ב - ...` (P.S. for a final nudge)

## Application to the bot (customer service, Hebrew)
- Open with first name + warm casual greeting; mirror the customer's register.
- Keep each message short — one idea per line — like the emails above and like WhatsApp.
- Use gender-inclusive phrasing (`את/ה`, `שלך`) until the customer's gender is known.
- Warm but professional; emoji only occasionally (a friendly ✅ / 📦 where it helps clarity).
- Plain, everyday Hebrew — no stiff corporate phrasing.

## Next step (recommended)
Because no real customer conversations exist yet, build a **synthetic Hebrew customer-message
dataset** organized by the 7 intents (§7 of CLAUDE.md) — realistic ways Israeli customers ask
about order status, product specs, cancellations, refunds, discounts, FAQ, and ambiguous
queries. Use it to (a) test the intent classifier and (b) tune replies to the voice above.
Replace/augment it with real transcripts once the pilot is live.
