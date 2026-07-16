# העלאת הבוט ל-Cloudflare — מדריך עצמאי

יש כאן שתי חבילות מוכנות. ברוב המקרים מתחילים מחבילה 1 (חינם, 5 דקות).

---

## חבילה 1: האתר (הצ'אט + הדשבורד) על Cloudflare Pages — חינם

מה מקבלים: כתובת ציבורית כמו `https://maniajeans.pages.dev` עם ממשק הצ'אט והדשבורד.
שימו לב: זה **הממשק בלבד** — המוח (השרת) צריך לרוץ במקום כלשהו שנגיש מהאינטרנט
(המחשב שלכם דרך Tunnel, שרת VPS, או חבילה 2 למטה).

### שלבים

1. **ערכו את `site/config.js`** — שורה אחת בלבד: הכתובת שבה השרת שלכם נגיש:
   ```js
   window.API_BASE = "https://הכתובת-של-השרת-שלכם";
   ```

2. **העלו את התיקייה `site/`** בלוח הבקרה של Cloudflare:
   - היכנסו ל-https://dash.cloudflare.com (חשבון חינמי מספיק)
   - **Workers & Pages → Create → Pages → Upload assets**
   - תנו שם לפרויקט (למשל `maniajeans-bot`) וגררו פנימה את שלושת הקבצים
     שבתיקיית `site/` (index.html, dashboard.html, config.js)
   - Deploy — וקיבלתם `https://maniajeans-bot.pages.dev`

3. **אבטחה (מומלץ):** בשרת, הגדירו ב-`.env`:
   ```
   ALLOWED_ORIGINS=https://maniajeans-bot.pages.dev
   ```
   כך רק האתר שלכם יוכל לדבר עם ה-API. בלי זה — כל אתר יכול.

### איך נותנים לשרת הביתי כתובת אינטרנט (Tunnel)

`cloudflared` כבר מותקן במחשב. כתובת זמנית (מתחלפת בכל הפעלה):
```bash
cloudflared tunnel --url http://localhost:8123
```
כתובת קבועה עם דומיין משלכם (חינם, דורש שהדומיין מנוהל ב-Cloudflare):
```bash
cloudflared tunnel login
cloudflared tunnel create maniajeans
cloudflared tunnel route dns maniajeans bot.yourdomain.com
cloudflared tunnel run --url http://localhost:8123 maniajeans
```
ואז ב-`config.js`: `window.API_BASE = "https://bot.yourdomain.com"`.

### עדכון האתר אחרי שינוי בממשק
```bash
python3 deploy/cloudflare/build_site.py     # בונה מחדש את site/ (לא דורס את config.js)
```
ואז העלאה מחודשת של התיקייה ב-Pages (Upload new deployment).

---

## חבילה 2: השרת המלא בענן של Cloudflare (Containers) — ~$5/חודש

מה מקבלים: הבוט רץ 24/7 בשרתי Cloudflare, בלי תלות במחשב שלכם.
דרישות: חשבון Cloudflare בתוכנית Workers Paid, Docker מותקן, ו-Node.js (בשביל wrangler).

```bash
cd /Users/israelmeir/chatbot/logs/whatsapp_agent      # חייבים להריץ מתיקיית הפרויקט!
npm install @cloudflare/containers                     # פעם אחת
npx wrangler login
npx wrangler deploy -c deploy/cloudflare/container/wrangler.jsonc
npx wrangler secret put ANTHROPIC_API_KEY -c deploy/cloudflare/container/wrangler.jsonc
```
מקבלים כתובת `https://maniajeans-bot.<account>.workers.dev` — אותה שמים ב-`config.js`
של חבילה 1 (או גולשים אליה ישירות — היא מגישה גם את הצ'אט).

### מגבלות הגרסה בענן (בכוונה, כדי לשמור על אימג' קטן)
- אין תמלול קולי מקומי (Whisper) — עובד רק אם מגדירים `OPENAI_API_KEY`.
- חיפוש הלקחים הסמנטי יורד לחיפוש מילות מפתח (אין sentence-transformers).
- הדיסק זמני: לוגים/לקחים מתאפסים בהפעלה מחדש של הקונטיינר.
  לפרודקשן אמיתי מחברים אחסון קבוע (R2/D1) — עבודה נפרדת.

---

## מה לא שייך ל-Cloudflare

חיבור הוואטסאפ (Evolution API) הוא שירות נפרד שרץ בדוקר (ראו `deploy/evolution/`).
אם השרת עבר לענן — מריצים את Evolution על אותו שרת/ענן ומעדכנים את `WA_GATEWAY_URL`.
