# Newsletters intake — Cloudflare Email Worker

Receives email at `newsletters@lavillerose.com`, parses the MIME envelope, stores the parsed content in Cloudflare KV with a 7-day TTL, and forwards the original to Gmail so subscription-confirmation links remain clickable.

## One-time setup

```powershell
cd worker-newsletters
npm install

# Auth (skip if already done from the other worker)
npx wrangler login

# Create the KV namespace and copy the printed id into wrangler.toml
npx wrangler kv namespace create NEWSLETTERS
# → paste the returned id into wrangler.toml under [[kv_namespaces]] (the line saying REPLACE_WITH_KV_NAMESPACE_ID)

# Set the forward-to address as a secret
npx wrangler secret put FORWARD_TO
# (paste your Gmail address, e.g. chaosskill@gmail.com)
```

## Deploy

```powershell
npm run deploy
```

Wrangler prints the Worker name. Note: this Worker has **no public URL** — it only handles incoming email events.

## Wire up Email Routing in the Cloudflare dashboard

1. Cloudflare dashboard → `lavillerose.com` → **Email** → **Email Routing**
2. **Email Addresses** → **Create address**
   - Custom address: `newsletters`
   - Action: **Send to a Worker**
   - Worker: `toulouse-news-newsletters`
   - Save
3. **Destination addresses** → if Gmail isn't already verified, add it. Cloudflare sends a verification email; click the link.

That's it. Mail to `newsletters@lavillerose.com` now hits the Worker.

## Test it

Send any email from your own account to `newsletters@lavillerose.com`. Within ~10 seconds:
- A copy should land in your Gmail (verifies the forward path).
- The Worker logs (in the CF dashboard → Workers → this worker → Logs) should show a successful KV put.

To inspect what's stored:

```powershell
npx wrangler kv key list --binding NEWSLETTERS
npx wrangler kv key get --binding NEWSLETTERS "<paste a key>"
```

## Subscribe to a newsletter

Use `newsletters@lavillerose.com` as your subscription email. The confirmation email will land in your Gmail (via forwarding). Click the confirm link as normal. From then on, every newsletter from that sender lands in KV ready for the daily digest pipeline to consume.
