# Toulouse Daily Digest — Project Spec

**Owner:** Ralph Ward
**Status:** v1 spec, ready for build
**Last updated:** 2026-05-10

---

## Goal

A daily Toulouse digest, **published as an Atom/RSS feed** (canonical output) at **`news.lavillerose.com`**, with two derived consumers:
- A **landing page** at the same URL — sectioned, browseable, with archive.
- A **daily email** delivered via Resend Broadcast to subscribers (Ralph + anyone who opts in via the landing page form).

Email lands in Gmail at **07:00 Europe/Paris** every day.

Coverage: news, events, cultural sorties, openings, civic announcements in and around Toulouse. Pulled from multiple Toulouse-specific sources, deduplicated across them, summarised by Claude.

**Completeness over filtering.** No interest-based filtering. Everything from the sources lands in the feed. Dedup clusters items covering the same story.

This is **v1**. A second project will follow for global news + tech YouTube; that one uses heavy filtering.

---

## Architectural principle: RSS-first

The Atom feed is the **single source of truth**. Both the landing page and the email are downstream consumers that read the feed and render it.

```
[8 sources] → fetch + clean → embed → cluster → synthesise
  → write/update feed.xml + per-category feeds (canonical, committed to repo,
                                                served via GitHub Pages on news.lavillerose.com)
  ├→ landing page (docs/index.html) renders today + archive
  └→ email renderer reads feed → HTML → Resend Broadcast → subscribers (Gmail)

Landing page also hosts a subscribe form
  → POST to Cloudflare Worker → Resend Contacts API → Audience grows
```

**Why RSS-first:**

- Single source of truth. No drift between feed, landing page, and email.
- Email and landing-page logic stay simple: read feed, render, present.
- Other delivery channels (Telegram, push) become trivial — they consume the feed.
- Past feed entries are the digest archive automatically.
- Replay/re-send without re-running the expensive pipeline.
- Testing is cleaner — validate feed independently of delivery.

---

## Sources (8)

| # | Source | Type | Fetch method | Cadence | Notes |
|---|--------|------|--------------|---------|-------|
| 1 | Toulouse Métropole OpenAgenda | Events | API via data.gouv.fr (`agenda-des-manifestations-culturelles`) | Daily-updated | Anchor source for events. Structured JSON. Covers 37 communes. |
| 2 | La Dépêche (Toulouse + Haute-Garonne) | News | RSS feed | Daily, multiple/day | Find correct RSS endpoint at build time. **Build last in v1; candidate to drop entirely if signal is too broad** — covers Toulouse + Haute-Garonne + Occitanie, lower Toulouse-specificity than the other 7 sources. |
| 3 | Actu Toulouse | News + sorties | HTML scrape | Daily, multiple/day | Verified 2026-05-10: no Toulouse-specific RSS exists; the global `/rss.xml` carries ~30 nationwide items with very few Toulouse hits. Scrape listing page `actu.fr/occitanie/toulouse_31555/` (≈20 article links per fetch). |
| 4 | L'Essentiel Toulouse | Curated daily digest | HTML scrape | Weekdays at 06:30 | URL pattern *guessed* as `lessentiel.fr/newsletter/toulouse/YYYY-MM-DD` — verify at build time. Email-first but archive is web-accessible. |
| 5 | Le Bonbon Toulouse | Lifestyle / sorties | HTML scrape | Random | Scrape category pages: `lebonbon.fr/toulouse/sorties/`, `/actu/`, `/loisirs/`. No RSS. |
| 6 | Clutch Toulouse | Culture / agenda | HTML scrape | Monthly print, online agenda updates more often | Scrape `clutchmag.fr` agenda continuously. |
| 7 | Toulouse Secret | Places, openings, food | HTML scrape | Random | Scrape `toulousesecret.com` homepage + recent articles. |
| 8 | Toulouscope | Mixed coverage | HTML scrape | Random | **Reuse Ralph's existing scrape code.** Verify it still works against current site — sites change HTML; budget a check-and-fix step. |

**Dropped from v1:**
- **Frimake** — app-only, behind login, user-generated meetups.
- **Toulouse By Night Fever** — Instagram/TikTok-first, `.com` is portfolio not feed.
- **Toulouse Magazine** — generic name, multiple candidates, unclear which is meant.

---

## Pipeline

### Step 1 — Fetch + clean

Source-specific fetchers return items shaped:

```python
{
    "source": "actu_toulouse",
    "url": "https://actu.fr/...",
    "title": "...",
    "published_at": "2026-05-10T14:32:00+02:00",
    "extracted_text": "...",
    "item_type": "news" | "event" | "place" | "culture",
    "event_date": "..." | None,
    "metadata": {...}
}
```

- **APIs** (OpenAgenda): direct JSON parse.
- **RSS** (La Dépêche, when built): `feedparser`. For each item, fetch the article URL and extract main content with `trafilatura`.
- **HTML scrapes** (rest, including Actu Toulouse): `requests` for listing pages, `trafilatura` for individual article content.

**Filter at fetch time:**
- News: items published in last 24h.
- Events: items with `event_date` in next 7 days.
- Places/openings/culture: items published in last 7 days.

### Step 2 — Embed

Embed `title + first ~500 words of extracted_text` using OpenAI `text-embedding-3-small`.

### Step 3 — Cluster (with 7-day rolling cache)

Maintain SQLite cache of last 7 days of items: embedding, source, title, URL, summary, `cluster_id`.

For each new item:
1. Compare embedding against cache (cosine similarity).
2. If similarity > **0.78** with any existing item, assign to that item's cluster.
3. Otherwise, start a new cluster.

The 0.78 threshold is a guess — tune after a week.

After clustering today's batch:
- **New clusters** → become new feed entries.
- **Existing clusters with new items today** → optionally bump the existing entry's `<updated>` and append an "Update:" block, *only* if the new coverage adds a meaningfully new angle.
- **Items near-identical to ones already in cache** (similarity > 0.92) → skip entirely.

### Step 4 — Synthesise per cluster

For each new or meaningfully-updated cluster, send to Claude Sonnet 4.6:

```
You are summarising Toulouse local news/events for a daily digest feed.

Cluster of {N} items covering the same topic, from sources: {sources}.

Generate:
1. A unified title (max 12 words, in French if originals are French, else English).
2. A 4-6 sentence summary covering: what's happening, what's notable, what's
   contested or unclear across sources if applicable, who's affected.
3. If multiple sources cover with meaningful framing differences, briefly
   note them. Skip if framings are identical.
4. Suggest which source to read for which angle, if relevant.

Constraints:
- Always preserve attribution; never present as original reporting.
- Don't smooth over uncertainty or contradictions between sources.
- Don't add facts not present in the source items.
- Keep each source's contribution traceable.

Items:
{for each item: source, title, excerpt (first 300 words)}

Return JSON:
{
  "title": "...",
  "summary": "...",
  "framing_note": "..." | null,
  "read_for": [{"source": "...", "angle": "..."}] | null
}
```

Cost: ~30 clusters/day × ~3k tokens ≈ ~$0.50/day, ~$15/month.

### Step 5 — Write Atom feeds (canonical + per-category)

Use `feedgen` library. Outputs into `docs/`:

- **`feed.xml`** — canonical "everything" Atom feed, all categories.
- **`feed-news.xml`** — entries with `category=news` only.
- **`feed-events.xml`** — entries with `category=event` only.
- **`feed-places.xml`** — entries with `category=place` only.
- **`feed-culture.xml`** — entries with `category=culture` only.

Per-category feeds let subscribers slice the firehose. Same data, filtered views.

**Granularity: one feed entry per cluster.** Not one entry per daily digest. RSS readers expect to skim individual stories.

Each entry contains:
- `<id>` — stable cluster ID (e.g. `toulouse-digest:cluster:{uuid}`)
- `<title>` — synthesised title
- `<published>` — when cluster first created
- `<updated>` — last update timestamp
- `<author>` — "Toulouse Digest"
- `<category>` — `news` / `event` / `place` / `culture`, plus source list
- `<content type="html">`:
  - Synthesised summary
  - Framing note if present
  - Event date if applicable
  - "Sources:" list with all source links
  - "Read for which angle" guide if present
- `<link rel="alternate">` — primary source link

**Feed metadata:**
- `<title>`: "Toulouse News" (or per-category: "Toulouse News — Événements", etc.)
- `<subtitle>`: "Auto-generated daily Toulouse digest"
- `<author>`: Ralph Ward
- Self-link points to `https://news.lavillerose.com/feed.xml` (or category variant).

**Retention:** keep last 50 entries live in the main feed (last 25 per category feed). Older entries roll off feed but stay in git history.

### Step 6 — Render landing page (consumes feed)

Reads `feed.xml` after it's written, renders `docs/index.html` as a sectioned landing page:

- **Header**: "TOULOUSE NEWS · {weekday} {date}".
- **Category nav**: links to per-category feed views (`/news`, `/events`, `/places`, `/culture`) and the canonical RSS subscribe link.
- **Sections** (mirroring the email):
  - **À la une** — top 3-5 by source diversity
  - **Actualités** — news, by recency
  - **Événements à venir** — events, by event date
  - **Sorties, lieux, ouvertures** — places
  - **Culture** — culture
  - **Toujours d'actu** — entries `updated` today but `published` earlier
- **Subscribe block**:
  - "Subscribe via RSS" → links to `feed.xml` and per-category feeds.
  - "Subscribe via email" → form posting to the Cloudflare Worker (see Step 8).
- **Archive link** — list of past digests by date (rendered from `feed.xml` history).

Mobile-friendly, clean, no images in v1. Rendering is templated (Jinja2 or string templates — keep simple).

### Step 7 — Render email + send via Resend Broadcast

After feed is written:

1. Read `feed.xml`.
2. Filter to entries where `published` or `updated` is today.
3. Group into the same sections as the landing page.
4. Render HTML email — mobile-friendly, plain styling, no images, no tracking pixels.
5. Send as a **Resend Broadcast** to a Resend Audience containing all subscribers.

The email is a **view** of the feed. The feed is the canonical artifact.

**Resend setup:**
- One Audience: `toulouse-news` (`RESEND_AUDIENCE_ID` secret).
- Sender domain: subdomain Ralph owns (e.g. `digest@news.lavillerose.com` or another subdomain). DKIM/SPF configured.
- Daily run uses Resend's `broadcasts/send` API.
- Initial subscriber: Ralph's Gmail (added manually to the Audience to bootstrap).

### Step 8 — Subscription endpoint (Cloudflare Worker)

A small Cloudflare Worker exposes `POST /subscribe` accepting `{email}`. It:
1. Validates the email format.
2. Calls Resend's `audiences/{id}/contacts` API to add the email.
3. Returns `200 {ok: true}` on success, `400` on invalid email, `409` on duplicate, `500` on Resend error.
4. Sets CORS to allow only `https://news.lavillerose.com` as origin.

**Why Cloudflare Worker (not direct from frontend):**
- Avoids exposing `RESEND_API_KEY` in frontend JS.
- Free tier (100k requests/day) far exceeds anything this project needs.
- Lives at `https://<worker-name>.<account>.workers.dev`; landing page form `fetch()`es it.

**Code lives in `worker/`** (same repo). Deploy via `wrangler deploy` — manual is fine for v1; can be wired into CI later.

**Worker secrets** (set via `wrangler secret put`):
- `RESEND_API_KEY`
- `RESEND_AUDIENCE_ID`

---

## Publishing the feed + landing page

- **GitHub Pages** serving from `/docs` of the repo. Free, static, reliable.
- **Subdomain**: `news.lavillerose.com` → CNAME to `ralphmartynward.github.io`. Apex `lavillerose.com` (Infomaniak FTP) untouched.
- **Setup steps**:
  1. Add CNAME record at DNS provider: `news` → `ralphmartynward.github.io`.
  2. Create `docs/CNAME` with content `news.lavillerose.com`.
  3. Enable GitHub Pages in repo settings: Source = `main` branch, folder = `/docs`.
  4. Wait for HTTPS provisioning (a few minutes).
- **Final URLs**:
  - Landing: `https://news.lavillerose.com/`
  - Main feed: `https://news.lavillerose.com/feed.xml`
  - Per-category: `/feed-news.xml`, `/feed-events.xml`, `/feed-places.xml`, `/feed-culture.xml`

---

## Infrastructure: GitHub Actions

- **Trigger**: cron `0 5 * * *` UTC (= 07:00 Europe/Paris in summer, 06:00 in winter — accept shift or handle DST in code).
- **Runner**: `ubuntu-latest`, Python 3.12.
- **Steps**:
  1. Checkout repo.
  2. Run pipeline → writes `data/items_seen.db`, `docs/feed.xml`, `docs/feed-*.xml`, `docs/index.html`.
  3. Send email Broadcast via Resend.
  4. Commit all updated artifacts back with `[skip ci]`. Single commit at end of run, after all writes succeed, to avoid desync on partial failure.
- **Repo secrets**:
  - `OPENAI_API_KEY`
  - `ANTHROPIC_API_KEY`
  - `RESEND_API_KEY`
  - `RESEND_AUDIENCE_ID`

---

## Repo structure

```
toulouse-digest/
├── .github/workflows/digest.yml
├── README.md
├── requirements.txt
├── src/
│   ├── main.py                # entry point, orchestrates pipeline
│   ├── fetchers/
│   │   ├── __init__.py
│   │   ├── openagenda.py
│   │   ├── la_depeche.py      # build last; candidate to drop in v1
│   │   ├── actu_toulouse.py   # HTML scrape (no Toulouse-specific RSS)
│   │   ├── lessentiel.py
│   │   ├── le_bonbon.py
│   │   ├── clutch.py
│   │   ├── toulouse_secret.py
│   │   └── toulouscope.py     # adapt Ralph's existing code
│   ├── embed.py               # OpenAI embedding wrapper
│   ├── cluster.py             # cosine sim + cache logic
│   ├── synthesise.py          # Claude wrapper
│   ├── feed.py                # Atom feed read/write (feedgen)
│   ├── landing.py             # render docs/index.html from feed
│   ├── render_email.py        # render email HTML from feed
│   ├── send.py                # Resend Broadcast wrapper
│   └── cache.py               # SQLite wrapper
├── data/
│   └── items_seen.db          # 7-day rolling cache (committed)
├── docs/                      # GitHub Pages root, served at news.lavillerose.com
│   ├── CNAME                  # contains: news.lavillerose.com
│   ├── index.html             # landing page (generated)
│   ├── feed.xml               # canonical Atom feed (generated, committed)
│   ├── feed-news.xml
│   ├── feed-events.xml
│   ├── feed-places.xml
│   └── feed-culture.xml
├── worker/                    # Cloudflare Worker (subscribe endpoint)
│   ├── src/index.ts
│   ├── wrangler.toml
│   └── package.json
├── templates/                 # landing-page + email templates
│   ├── landing.html.j2
│   └── email.html.j2
├── tests/
│   └── ...
└── prompts/
    └── synthesise.md          # version-controlled synthesis prompt
```

---

## Build order

1. **Scaffolding**: repo, deps, env vars, GitHub Actions workflow running hello-world. ✓ done
2. **First fetcher end-to-end**: Actu Toulouse (HTML scrape — listing page extraction). Establishes the article-fetcher pattern. ✓ done
3. **Feed write path + GitHub Pages on `news.lavillerose.com`**: skip clustering and synthesis. Write today's items as raw entries to `docs/feed.xml`. Validate via W3C feed validator. Set up GitHub Pages, attach `news.lavillerose.com` via CNAME + DNS. Subscribe to the live URL with a feed reader to confirm.
4. **Landing page** (minimal): `docs/index.html` lists today's entries from the feed, has "Subscribe via RSS" + placeholder "Subscribe via email" form (form not wired yet).
5. **Subscription pipeline**: deploy Cloudflare Worker exposing `POST /subscribe`. Wire landing-page form to it. Confirm signups arrive in Resend Audience.
6. **Email renderer + Broadcast send**: render today's HTML email from the feed, send via Resend Broadcast to the Audience. Subscribe Ralph's Gmail; confirm daily email arrives.
7. **Add embeddings + cache**: SQLite, cluster within today's items.
8. **Add synthesise step**: Claude generates per-cluster entry content. Feed entries become richer.
9. **Add per-category feeds**: derive `feed-news.xml` etc. from main feed by category filter. Update landing page nav.
10. **Add the other fetchers, one at a time**, in order of Toulouse-specificity: OpenAgenda, L'Essentiel (verify URL first), Le Bonbon, Clutch, Toulouse Secret, Toulouscope (verify code first). Then **La Dépêche last (or drop entirely if v1 is feeling complete enough without it)**.
11. **Tune cluster threshold** after a week of real output.
12. **Polish landing page + email styling**.

Ship feed + landing + email + subscription with one source before adding the rest.

---

## Known unknowns / decisions deferred

- **Cluster threshold** (0.78 is a guess).
- **Image handling**: skipped in v1.
- **DST handling**: GitHub Actions cron is UTC.
- **Toulouscope scraper currency**: untested against current HTML structure.
- **Updating existing feed entries**: deciding when a cluster gets `<updated>` bumped vs left alone. Start conservative: only update if a new source covers the cluster for the first time.
- **Feed entry retention** (50 main / 25 per-category — guesses).
- **La Dépêche inclusion**: reassess at step 10 — if v1 already feels rich enough, drop it.
- **Sender domain for Resend**: which subdomain to use as the email From: address (e.g. `digest@news.lavillerose.com`). DKIM/SPF setup confirmed at step 6.

---

## Out of scope for v1

- Global news / tech YouTube digest (separate project).
- Spotify / music trending.
- Interest-based filtering.
- Mobile app, PDF export.
- Double opt-in confirmation emails (single opt-in is fine for v1; revisit if it scales).
- Unsubscribe flow beyond Resend's built-in Broadcast unsubscribe link.
- AI-generated unique articles replacing source content (deliberately rejected — would shift from aggregation to substitutive content production, raises legal/ethical issues, strips attribution value, can hallucinate).

---

## Cost estimate

| Item | Monthly cost |
|------|-------------|
| OpenAI embeddings (text-embedding-3-small) | ~$0.30 |
| Claude Sonnet 4.6 synthesis (richer output) | ~$15 |
| Resend (free tier: 3k emails/month, audiences) | $0 |
| GitHub Actions (free for personal repos) | $0 |
| GitHub Pages (free) | $0 |
| Cloudflare Workers (free: 100k req/day) | $0 |
| **Total** | **~$15/month** |

---

## Done criteria for v1

- Atom feed at `https://news.lavillerose.com/feed.xml`, valid per W3C feed validator.
- Per-category feeds also valid and reachable.
- Landing page at `https://news.lavillerose.com/` renders feed entries grouped by section.
- Subscribe-via-email form on landing page works end-to-end: submit → added to Resend Audience → confirmation received.
- Daily email Broadcast arrives in Gmail at 07:00, 14 consecutive days without manual intervention.
- All 7 (or 8) sources contribute items to at least one digest in that period.
- Dedup visibly works (entries with multi-source coverage visible in feed).
- Same item never shown two days running.
- Cluster threshold tuned based on real output.
- Email content matches feed content (no drift).
