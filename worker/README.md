# Subscribe endpoint — Cloudflare Worker

Receives `POST /subscribe` from the landing-page form, validates the email, and adds it to the Resend Audience. CORS locked to `https://news.lavillerose.com`. On a genuinely new subscription (not a re-submit of an already-subscribed address), also sends a one-line notification email via Resend to `ALERT_EMAIL_TO` — fire-and-forget (`ctx.waitUntil`), so a failure there never affects the subscribe response itself.

## One-time setup

```powershell
cd worker
npm install

# Authenticate wrangler with your Cloudflare account
npx wrangler login

# Set the secrets the Worker reads at runtime
npx wrangler secret put RESEND_API_KEY
# (paste your Resend API key when prompted)

npx wrangler secret put RESEND_AUDIENCE_ID
# (paste 1ab27ddc-0906-4a77-bdf9-4558fb29d77a)

npx wrangler secret put ALERT_EMAIL_TO
# (paste the address you want new-subscriber alerts sent to)
```

## Deploy

```powershell
npm run deploy
```

Wrangler will print the Worker's URL — something like `https://toulouse-news-subscribe.<your-account>.workers.dev`. The endpoint is `<that-url>/subscribe`.

## Smoke test

```powershell
$worker = "https://toulouse-news-subscribe.<your-account>.workers.dev"
Invoke-RestMethod -Uri "$worker/subscribe" -Method POST -ContentType "application/json" -Body '{"email":"test@example.com"}'
```

Expected: `{ ok = True }`. Re-running the same email returns `{ ok = True; note = already_subscribed }`.

## Local dev (optional)

```powershell
npm run dev
```

Wrangler runs the Worker on `http://localhost:8787`. Set local secrets in `worker/.dev.vars` (gitignored):

```
RESEND_API_KEY=re_xxx
RESEND_AUDIENCE_ID=1ab27ddc-0906-4a77-bdf9-4558fb29d77a
ALERT_EMAIL_TO=contact@mavillerose.com
```
