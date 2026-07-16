"""Weekly business report — aggregates the last 7 days from the jsonl logs and asks Claude
(Haiku) to write an executive summary + concrete improvement recommendations, grounded ONLY in
the computed numbers (truth anchor: the analyst never invents a figure that isn't in the metrics).

Sections: volume, closures (self-served + resolved tickets + executed actions), satisfaction &
how many customers got upset, handoffs & why, money (real API cost vs. estimated human-time saved
= profit/loss, plus sales opportunity), what the bot learned, and open knowledge gaps.

Generate:  python -m app.analytics.weekly_report          (saves + prints; emails if SMTP set)
API:        GET /report/weekly?days=7  (JSON) · GET /report (HTML)
"""
import json
import time
import datetime as dt
from collections import Counter
from typing import Dict, Any, List, Optional

from app.config import DATA_DIR, settings
from app.analytics import costs
from app.brain.intents import _LABELS_HE

_ANALYST_SYSTEM = (
    "אתה אנליסט שירות לקוחות בכיר של חנות אופנה. תקבל נתוני ביצועים של שבוע (JSON) של בוט "
    "שירות/מכירות בוואטסאפ. כתוב בעברית, ענייני ומדויק, בלי סיסמאות. חובה: התבסס אך ורק על "
    "המספרים שקיבלת — אל תמציא נתון שלא מופיע ב-JSON. החזר JSON יחיד בדיוק במבנה: "
    '{"summary": "2-3 משפטי תקציר מנהלים", "wins": ["2-3 נקודות חוזק קצרות"], '
    '"improvements": [{"issue": "הבעיה + הנתון שמעיד עליה", "action": "צעד קונקרטי לשיפור"}], '
    '"risk": "הדבר הכי דחוף לטפל בו השבוע, משפט אחד"}. '
    "המלצות שיפור: גזור אותן מהנתונים — אחוז 'לא זוהה' גבוה, סיבות העברה חוזרות, פערי ידע פתוחים "
    "(כרטיסים), שיחות עם חוסר שביעות רצון, לקוחות שהתעצבנו. 3 עד 5 המלצות, כל אחת משפט-שניים בלבד. "
    "החזר אך ורק את ה-JSON, בלי טקסט לפניו או אחריו, וקצר — לא יותר מ-5 המלצות."
)


def _read_jsonl(path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _start_ts(days: int) -> str:
    return (dt.datetime.now() - dt.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _in_window(row: Dict[str, Any], start: str) -> bool:
    return str(row.get("ts", "")) >= start


def gather(days: int = 7) -> Dict[str, Any]:
    """Compute the week's metrics from the logs (no LLM). Everything here is real, not estimated,
    except the clearly-labeled money model."""
    start = _start_ts(days)
    logs = DATA_DIR / "logs"
    learn = DATA_DIR / "learning"

    convs = [c for c in _read_jsonl(logs / "conversations.jsonl") if _in_window(c, start)]
    handoffs = [h for h in _read_jsonl(logs / "handoffs.jsonl") if _in_window(h, start)]
    actions = [a for a in _read_jsonl(logs / "actions.jsonl") if _in_window(a, start)]
    feedback = [f for f in _read_jsonl(learn / "feedback.jsonl") if _in_window(f, start)]
    lessons_week = [l for l in _read_jsonl(learn / "lessons.jsonl") if _in_window(l, start)]
    all_tickets = _read_jsonl(logs / "tickets.jsonl")

    # tickets can be rewritten on resolve (append + full rewrite) — dedupe by id, last wins
    tickets_by_id: Dict[str, Dict[str, Any]] = {t.get("id"): t for t in all_tickets}
    tickets = list(tickets_by_id.values())
    opened_week = [t for t in tickets if _in_window(t, start)]
    open_now = [t for t in tickets if t.get("status") == "open"]
    resolved_week = [t for t in tickets if t.get("status") == "resolved" and _in_window(t, start)]

    # --- volume ---
    sessions = {c.get("session_id") for c in convs}
    messages = len(convs)
    intents = Counter(c.get("intent", "?") for c in convs)

    # --- sentiment: how many customers got upset ---
    upset_msgs = sum(1 for c in convs if c.get("sentiment") in ("annoyed", "angry"))
    angry_msgs = sum(1 for c in convs if c.get("sentiment") == "angry")
    upset_sessions = {c.get("session_id") for c in convs if c.get("sentiment") in ("annoyed", "angry")}

    # --- handoffs (human takeovers) ---
    handoff_sessions = {c.get("session_id") for c in convs if c.get("handoff")}
    handoff_reasons = Counter((h.get("reason") or "לא צוין")[:60] for h in handoffs)

    # --- closures / self-serve ---
    self_served = sessions - handoff_sessions           # bot handled fully, no human needed
    executed_actions = sum(1 for a in actions if a.get("result") == "executed")

    # --- satisfaction ---
    ratings = Counter(f.get("rating", "unknown") for f in feedback)

    # --- money (labeled ESTIMATE except api_cost) ---
    spend = costs.spend_since(start)
    api_cost_usd = spend["usd"]
    api_cost_ils = api_cost_usd * settings.USD_TO_ILS
    savings_ils = len(self_served) * settings.COST_PER_HUMAN_CONTACT_ILS   # human-contact deflection
    net_ils = savings_ils - api_cost_ils
    # sales opportunity: product-interest sessions × assumed conversion × avg order value
    interest_sessions = {c.get("session_id") for c in convs
                         if c.get("intent") in ("product_info", "discount")}
    sales_potential_ils = len(interest_sessions) * settings.ASSUMED_CONVERSION * settings.AVG_ORDER_VALUE_ILS

    return {
        "period": {
            "days": days,
            "from": start[:10],
            "to": dt.datetime.now().strftime("%Y-%m-%d"),
            "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        },
        "volume": {
            "conversations": len(sessions),
            "messages": messages,
            "customers": len(sessions),   # in production the session id is the customer's number
            "by_intent": {_LABELS_HE.get(k, k): v for k, v in intents.most_common()},
        },
        "closures": {
            "self_served_sessions": len(self_served),
            "resolved_tickets": len(resolved_week),
            "executed_actions": executed_actions,
            "handoff_sessions": len(handoff_sessions),
            "self_serve_rate_pct": round(100 * len(self_served) / len(sessions), 1) if sessions else 0,
        },
        "satisfaction": {
            "satisfied": ratings.get("satisfied", 0),
            "neutral": ratings.get("neutral", 0),
            "dissatisfied": ratings.get("dissatisfied", 0),
            "assessed": len(feedback),
            "upset_customers": len(upset_sessions),
            "upset_messages": upset_msgs,
            "angry_messages": angry_msgs,
        },
        "handoffs": {
            "count": len(handoff_sessions),
            "top_reasons": handoff_reasons.most_common(5),
        },
        "money": {
            "api_cost_usd": round(api_cost_usd, 4),
            "api_cost_ils": round(api_cost_ils, 2),
            "est_savings_ils": round(savings_ils, 2),
            "est_net_ils": round(net_ils, 2),
            "est_sales_potential_ils": round(sales_potential_ils, 2),
            "assumptions": {
                "cost_per_human_contact_ils": settings.COST_PER_HUMAN_CONTACT_ILS,
                "usd_to_ils": settings.USD_TO_ILS,
                "assumed_conversion": settings.ASSUMED_CONVERSION,
                "avg_order_value_ils": settings.AVG_ORDER_VALUE_ILS,
            },
            "by_model": spend["by_model"],
        },
        "learning": {
            "lessons_added": len(lessons_week),
            "open_knowledge_gaps": [
                {"topic": t.get("topic"), "question": (t.get("question") or "")[:120]}
                for t in open_now
            ][:10],
            "open_gap_count": len(open_now),
            "tickets_opened": len(opened_week),
        },
    }


def analyze(metrics: Dict[str, Any], client=None) -> Dict[str, Any]:
    """Ask Haiku for an exec summary + improvement recommendations, grounded in `metrics`."""
    if client is None:
        return {"summary": "", "wins": [], "improvements": [], "risk": "", "_no_llm": True}
    try:
        resp = client.messages.create(
            model=settings.CLAUDE_CLASSIFIER_MODEL,
            max_tokens=2000,
            system=_ANALYST_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(metrics, ensure_ascii=False)}],
        )
        costs.record_usage(settings.CLAUDE_CLASSIFIER_MODEL, getattr(resp, "usage", None))
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        if text.startswith("```"):                       # strip a ```json fence if present
            text = text.split("```")[1].lstrip("json").strip()
        s, e = text.find("{"), text.rfind("}")
        if s >= 0 and e > s:
            try:
                return json.loads(text[s:e + 1])
            except json.JSONDecodeError:
                pass
        return {"summary": text[:800], "wins": [], "improvements": [], "risk": ""}
    except Exception as ex:
        return {"summary": "", "wins": [], "improvements": [],
                "risk": "", "_error": f"{type(ex).__name__}: {str(ex)[:140]}"}


def build(days: int = 7, client=None) -> Dict[str, Any]:
    if client is None and settings.has_claude:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    metrics = gather(days)
    analysis = analyze(metrics, client)
    return {"metrics": metrics, "analysis": analysis}


# ---------------- rendering ----------------
def render_text(report: Dict[str, Any]) -> str:
    m, a = report["metrics"], report["analysis"]
    p, v, c, s, h, mo, l = (m["period"], m["volume"], m["closures"], m["satisfaction"],
                            m["handoffs"], m["money"], m["learning"])
    L = [
        f"📊 דוח שבועי — {settings.STORE_NAME}",
        f"תקופה: {p['from']} עד {p['to']}  ·  הופק: {p['generated_at']}",
        "",
        "— תקציר —",
        a.get("summary") or "(אין תקציר — לא הופעל ניתוח AI)",
        "",
        "— נפח —",
        f"שיחות: {v['conversations']}  ·  הודעות: {v['messages']}  ·  לקוחות: {v['customers']}",
        "לפי נושא: " + ("  ".join(f"{k}={n}" for k, n in v["by_intent"].items()) or "—"),
        "",
        "— סגירות —",
        f"נסגרו ע\"י הבוט לבד: {c['self_served_sessions']} ({c['self_serve_rate_pct']}%)",
        f"כרטיסי שירות שנסגרו: {c['resolved_tickets']}  ·  פעולות שבוצעו (ביטולים): {c['executed_actions']}",
        f"הועברו לנציג: {c['handoff_sessions']}",
        "",
        "— שביעות רצון —",
        f"מרוצים: {s['satisfied']}  ·  ניטרלי: {s['neutral']}  ·  לא מרוצים: {s['dissatisfied']}  (הוערכו {s['assessed']})",
        f"לקוחות שהתעצבנו: {s['upset_customers']}  (מתוכם הודעות בכעס: {s['angry_messages']})",
        "",
        "— העברות לנציג —",
        f"סה\"כ: {h['count']}",
        "סיבות עיקריות: " + ("  ".join(f"{r} ({n})" for r, n in h["top_reasons"]) or "—"),
        "",
        "— כסף (רווח/הפסד) —",
        f"עלות API בפועל: ${mo['api_cost_usd']}  (≈ {mo['api_cost_ils']}₪)",
        f"חיסכון מוערך (שיחות שלא הצריכו נציג): {mo['est_savings_ils']}₪",
        f"נטו מוערך: {mo['est_net_ils']}₪",
        f"פוטנציאל מכירה מיוחס: {mo['est_sales_potential_ils']}₪",
        f"(הנחות: {mo['assumptions']['cost_per_human_contact_ils']}₪/פניית-אנוש, "
        f"המרה {int(mo['assumptions']['assumed_conversion']*100)}%, "
        f"עסקה ממוצעת {mo['assumptions']['avg_order_value_ils']}₪, דולר={mo['assumptions']['usd_to_ils']})",
        "",
        "— למידה ופערי ידע —",
        f"לקחים חדשים שנלמדו: {l['lessons_added']}  ·  פערי ידע פתוחים: {l['open_gap_count']}",
    ]
    for g in l["open_knowledge_gaps"]:
        L.append(f"   • {g['topic']}: {g['question']}")
    if a.get("wins"):
        L += ["", "— מה עובד טוב —"] + [f"✅ {w}" for w in a["wins"]]
    if a.get("improvements"):
        L += ["", "— מה צריך לשפר (המלצות הבוט) —"]
        for i in a["improvements"]:
            if isinstance(i, dict):
                L.append(f"🔧 {i.get('issue', '')}\n     → {i.get('action', '')}")
            else:
                L.append(f"🔧 {i}")
    if a.get("risk"):
        L += ["", f"⚠️ הכי דחוף: {a['risk']}"]
    return "\n".join(L)


def render_html(report: Dict[str, Any]) -> str:
    m, a = report["metrics"], report["analysis"]
    p, v, c, s, h, mo, l = (m["period"], m["volume"], m["closures"], m["satisfaction"],
                            m["handoffs"], m["money"], m["learning"])

    def kpi(label, value, sub=""):
        sub = f"<div class='sub'>{sub}</div>" if sub else ""
        return f"<div class='kpi'><div class='v'>{value}</div><div class='l'>{label}</div>{sub}</div>"

    net = mo["est_net_ils"]
    net_color = "#1d7a4d" if net >= 0 else "#c0392b"
    intents_rows = "".join(
        f"<tr><td>{k}</td><td>{n}</td></tr>" for k, n in v["by_intent"].items()) or "<tr><td>—</td><td></td></tr>"
    reasons = "".join(f"<li>{r} <b>({n})</b></li>" for r, n in h["top_reasons"]) or "<li>—</li>"
    gaps = "".join(f"<li><b>{g['topic']}</b> — {g['question']}</li>"
                   for g in l["open_knowledge_gaps"]) or "<li>אין פערים פתוחים 🎉</li>"
    wins = "".join(f"<li>{w}</li>" for w in a.get("wins", [])) or "<li>—</li>"
    imps = ""
    for i in a.get("improvements", []):
        if isinstance(i, dict):
            imps += f"<li><b>{i.get('issue','')}</b><br><span class='act'>→ {i.get('action','')}</span></li>"
        else:
            imps += f"<li>{i}</li>"
    imps = imps or "<li>—</li>"
    risk = f"<div class='risk'>⚠️ הכי דחוף לשבוע: {a['risk']}</div>" if a.get("risk") else ""
    summary = a.get("summary") or "לא הופעל ניתוח AI (אין מפתח Claude פעיל)."

    return f"""<!DOCTYPE html><html lang="he" dir="rtl"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>דוח שבועי — {settings.STORE_NAME}</title><style>
*{{box-sizing:border-box;font-family:Heebo,"Segoe UI",Arial,sans-serif}}
body{{margin:0;background:#f4f6f8;color:#1f2d3d;padding:24px}}
.wrap{{max-width:880px;margin:0 auto}}
h1{{font-size:22px;margin:0 0 2px}} .period{{color:#7b8794;font-size:13px;margin-bottom:18px}}
.card{{background:#fff;border-radius:14px;padding:18px 20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.07)}}
.card h2{{font-size:15px;margin:0 0 12px;color:#334}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:12px}}
.kpi{{background:#f7f9fb;border-radius:10px;padding:12px;text-align:center}}
.kpi .v{{font-size:24px;font-weight:800;color:#075e54}} .kpi .l{{font-size:12px;color:#667}}
.kpi .sub{{font-size:11px;color:#98a2b3;margin-top:2px}}
.summary{{background:#eef6f2;border-inline-start:4px solid #128c7e;padding:12px 16px;border-radius:8px;line-height:1.6}}
table{{width:100%;border-collapse:collapse;font-size:14px}} td{{padding:5px 6px;border-bottom:1px solid #eef}}
ul{{margin:0;padding-inline-start:20px;line-height:1.7}} .act{{color:#128c7e;font-size:13px}}
.money{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}}
.money .m{{padding:12px;border-radius:10px;background:#f7f9fb;text-align:center}}
.money .m .mv{{font-size:20px;font-weight:800}} .money .m .ml{{font-size:12px;color:#667}}
.assump{{font-size:11px;color:#98a2b3;margin-top:10px}}
.risk{{background:#fff4e5;border-inline-start:4px solid #f0932b;padding:10px 14px;border-radius:8px;margin-top:12px}}
.imp li{{margin-bottom:8px}}
</style></head><body><div class="wrap">
<h1>📊 דוח שבועי — {settings.STORE_NAME}</h1>
<div class="period">{p['from']} — {p['to']} · הופק {p['generated_at']}</div>

<div class="card"><h2>תקציר מנהלים</h2><div class="summary">{summary}</div>{risk}</div>

<div class="card"><h2>נפח פעילות</h2><div class="kpis">
{kpi("שיחות", v['conversations'])}{kpi("הודעות", v['messages'])}
{kpi("נסגרו לבד", c['self_served_sessions'], f"{c['self_serve_rate_pct']}% מהשיחות")}
{kpi("הועברו לנציג", c['handoff_sessions'])}
{kpi("ביטולים שבוצעו", c['executed_actions'])}{kpi("כרטיסים נסגרו", c['resolved_tickets'])}
</div></div>

<div class="card"><h2>שביעות רצון</h2><div class="kpis">
{kpi("מרוצים", s['satisfied'])}{kpi("ניטרלי", s['neutral'])}{kpi("לא מרוצים", s['dissatisfied'])}
{kpi("התעצבנו", s['upset_customers'], f"{s['angry_messages']} הודעות בכעס")}
</div></div>

<div class="card"><h2>כסף — רווח / הפסד (הערכה)</h2><div class="money">
<div class="m"><div class="mv">${mo['api_cost_usd']}</div><div class="ml">עלות API בפועל (≈{mo['api_cost_ils']}₪)</div></div>
<div class="m"><div class="mv">{mo['est_savings_ils']}₪</div><div class="ml">חיסכון בכוח-אדם</div></div>
<div class="m"><div class="mv" style="color:{net_color}">{mo['est_net_ils']}₪</div><div class="ml">נטו מוערך</div></div>
<div class="m"><div class="mv">{mo['est_sales_potential_ils']}₪</div><div class="ml">פוטנציאל מכירה</div></div>
</div><div class="assump">הנחות מודל: {mo['assumptions']['cost_per_human_contact_ils']}₪ לפניית-אנוש שנחסכה ·
המרה {int(mo['assumptions']['assumed_conversion']*100)}% · עסקה ממוצעת {mo['assumptions']['avg_order_value_ils']}₪ ·
דולר={mo['assumptions']['usd_to_ils']}₪. עלות ה-API מדודה בפועל; השאר הערכות ניתנות לכיוונון.</div></div>

<div class="card"><h2>נושאים והעברות לנציג</h2>
<div style="display:flex;gap:24px;flex-wrap:wrap">
<div style="flex:1;min-width:220px"><table>{intents_rows}</table></div>
<div style="flex:1;min-width:220px"><b>סיבות העברה עיקריות</b><ul>{reasons}</ul></div>
</div></div>

<div class="card"><h2>מה עובד טוב</h2><ul>{wins}</ul></div>
<div class="card"><h2>🔧 מה צריך לשפר (המלצות מנותחות מהנתונים)</h2><ul class="imp">{imps}</ul></div>

<div class="card"><h2>למידה ופערי ידע</h2>
<p>לקחים חדשים שנלמדו השבוע: <b>{l['lessons_added']}</b> · פערי ידע פתוחים: <b>{l['open_gap_count']}</b></p>
<ul>{gaps}</ul></div>

<div class="period">נוצר אוטומטית ע"י בוט השירות · הנתונים מהלוגים של 7 הימים האחרונים</div>
</div></body></html>"""


# ---------------- delivery ----------------
def save(report: Dict[str, Any]) -> Dict[str, str]:
    out = DATA_DIR / "reports"
    out.mkdir(parents=True, exist_ok=True)
    stamp = report["metrics"]["period"]["to"]
    html_path = out / f"weekly_{stamp}.html"
    txt_path = out / f"weekly_{stamp}.txt"
    html_path.write_text(render_html(report), encoding="utf-8")
    txt_path.write_text(render_text(report), encoding="utf-8")
    return {"html": str(html_path), "txt": str(txt_path)}


def render_whatsapp(report: Dict[str, Any]) -> str:
    """A condensed, phone-friendly version for a WhatsApp message (WhatsApp bold = *text*)."""
    m, a = report["metrics"], report["analysis"]
    p, v, c, s, h, mo, l = (m["period"], m["volume"], m["closures"], m["satisfaction"],
                            m["handoffs"], m["money"], m["learning"])
    L = [
        f"*📊 דוח שבועי — {settings.STORE_NAME}*",
        f"{p['from']} – {p['to']}",
        "",
        a.get("summary") or "",
        "",
        f"*נפח:* {v['conversations']} שיחות · {v['messages']} הודעות",
        f"*נסגרו לבד:* {c['self_served_sessions']} ({c['self_serve_rate_pct']}%) · *לנציג:* {c['handoff_sessions']}",
        f"*שביעות רצון:* {s['satisfied']} מרוצים · {s['dissatisfied']} לא · *התעצבנו:* {s['upset_customers']}",
        f"*כסף:* עלות API {mo['api_cost_ils']}₪ · חיסכון מוערך {mo['est_savings_ils']}₪ · *נטו {mo['est_net_ils']}₪*",
        f"*פערי ידע פתוחים:* {l['open_gap_count']} · לקחים חדשים: {l['lessons_added']}",
    ]
    imps = a.get("improvements", [])
    if imps:
        L += ["", "*🔧 לשיפור:*"]
        for i in imps[:4]:
            L.append(f"• {i.get('action', i) if isinstance(i, dict) else i}")
    if a.get("risk"):
        L += ["", f"⚠️ *הכי דחוף:* {a['risk']}"]
    return "\n".join(x for x in L if x is not None)


def whatsapp(report: Dict[str, Any]) -> Optional[str]:
    """Send the report to the owner's WhatsApp via the gateway. No-op (with a reason) until the
    Evolution API gateway is configured (M1). Returns None on success, else a reason/error string."""
    to = settings.REPORT_WHATSAPP_TO
    if not to:
        return "REPORT_WHATSAPP_TO not set (owner's WhatsApp number)"
    from app.whatsapp import gateway
    res = gateway.send_message(to, render_whatsapp(report))
    if res.get("sent"):
        return None
    return res.get("reason") or res.get("error") or f"status {res.get('status')}"


def email(report: Dict[str, Any]) -> Optional[str]:
    """Send the report by email if SMTP is configured. Returns None on success, else an error."""
    if not settings.has_smtp:
        return "SMTP not configured (SMTP_USER/SMTP_PASS/REPORT_EMAIL_TO)"
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    p = report["metrics"]["period"]
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📊 דוח שבועי — {settings.STORE_NAME} ({p['from']}–{p['to']})"
    msg["From"] = settings.SMTP_USER
    msg["To"] = settings.REPORT_EMAIL_TO
    msg.attach(MIMEText(render_text(report), "plain", "utf-8"))
    msg.attach(MIMEText(render_html(report), "html", "utf-8"))
    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as srv:
            srv.starttls()
            srv.login(settings.SMTP_USER, settings.SMTP_PASS)
            srv.sendmail(settings.SMTP_USER, [settings.REPORT_EMAIL_TO], msg.as_string())
        return None
    except Exception as ex:
        return f"{type(ex).__name__}: {str(ex)[:160]}"


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Weekly report for the WhatsApp bot")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--email", action="store_true", help="also send by email if SMTP is set")
    ap.add_argument("--whatsapp", action="store_true", help="also send to owner's WhatsApp (M1)")
    ap.add_argument("--quiet", action="store_true", help="don't print the text report")
    a = ap.parse_args()

    report = build(days=a.days)
    paths = save(report)
    if not a.quiet:
        print(render_text(report))
        print("\n" + "─" * 40)
    print(f"נשמר: {paths['html']}")
    if a.email:
        err = email(report)
        print("📧 נשלח במייל." if err is None else f"📧 שליחת מייל נכשלה: {err}")
    if a.whatsapp:
        err = whatsapp(report)
        print("📱 נשלח בוואטסאפ." if err is None
              else f"📱 וואטסאפ עדיין לא פעיל ({err}) — הדוח נשמר וזמין בדשבורד.")


if __name__ == "__main__":
    main()
