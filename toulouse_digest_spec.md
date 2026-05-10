# Toulouse Daily Digest — Project Spec

**Owner:** Ralph Ward
**Status:** v1 spec, ready for build
**Last updated:** 2026-05-10

---

## Goal

A daily email digest landing in Gmail at **07:00 Europe/Paris**, summarising what's happening in and around Toulouse: news, events, cultural sorties, openings, civic announcements. Pulled from multiple Toulouse-specific sources, deduplicated across them, summarised by Claude.

**Completeness over filtering.** No interest-based filtering. Everything that comes through the sources lands in the digest. Dedup clusters items covering the same story.

This is **v1**. A second project will follow for global news + tech YouTube; that one uses heavy filtering. Different beast.

---

## Sources (8)

| # | Source | Type | Fetch method | Cadence of source | Notes |
|---|--------|------|--------------|-------------------|-------|
| 1 | Toulouse Métropole OpenAgenda | Events | API via data.gouv.fr (`agenda-des-manifestations-culturelles`) | Daily-updated | Anchor source for events. Structured JSON. Covers municipal/cultural events across the 37 communes. |
| 2 | La Dépêche (Toulouse + Haute-Garonne) | News | RSS feed | Daily, multiple/day | Find correct RSS endpoint at build time — try `ladepeche.fr/rss/` or feed discovery. |
| 3 | Actu Toulouse | News + sorties | RSS feed | Daily, multiple/day | `actu.fr/occitanie/toulouse_31555/` should expose RSS. |
| 4 | L'Essentiel Toulouse | Curated daily digest | HTML scrape | Weekdays at 06:30 | URL pattern *guessed* as `lessentiel.fr/newsletter/toulouse/YYYY-MM-DD` — verify at build time. Email-first newsletter, but archive is web-accessible. |
| 5 | Le Bonbon Toulouse | Lifestyle / sorties | HTML scrape | Random / multiple per week | Scrape category pages: `lebonbon.fr/toulouse/sorties/`, `/actu/`, `/loisirs/`. No RSS found. |
| 6 | Clutch Toulouse | Culture / agenda | HTML scrape | Monthly print, online agenda updates more often | Scrape `clutchmag.fr` agenda page continuously, not just on new issue release. |
| 7 | Toulouse Secret | Places, openings, food | HTML scrape | Random / multiple per week | Scrape `toulousesecret.com` homepage + recent articles. |
| 8 | Toulouscope | Mixed coverage | HTML scrape | Random | **Reuse Ralph's existing scrape code.** Verify it still works against current site before relying on it — sites change HTML; budget a check-and-fix step. |

**Dropped from v1 (and why):**
- **Frimake** — app-only, behind login, user-generated meetups not events.
- **Toulouse By Night Fever** — Instagram/TikTok-first, `.com` is portfolio not feed.
- **Toulouse Magazine** — generic name, multiple candidates, unclear which Ralph meant.

---

## Pipeline architecture

```
[8 sources] → fetch + clean → embed → cluster (with 7-day cache)
  → synthesise per cluster (Claude) → assemble HTML email → send via Resend → Gmail
```

### Step 1 — Fetch + clean

For each source, run a source-specific fetcher returning a list of items with this shape:

```python
{
    "source": "la_depeche",
    "url": "https://www.ladepeche.fr/...",
    "title": "...",
    "published_at": "2026-05-10T14:32:00+02:00",  # ISO 8601
    "raw_html": "...",  # or None for API sources
    "extracted_text": "...",  # cleaned main content
    "item_type": "news" | "event" | "place" | "culture",
    "event_date": "..." | None,  # only for events
    "metadata": {...}  # source-specific extras
}
```

- **APIs** (OpenAgenda): direct JSON parse, no extraction needed.
- **RSS** (La Dépêche, Actu Toulouse): `feedparser` library. For each item, fetch the article URL and extract main content with `trafilatura`.
- **HTML scrapes** (everything else): `requests` + `BeautifulSoup` for listing pages, `trafilatura` for individual article content.

**Filter at fetch time:**
- News articles: only items published in the last 24h.
- Events: only items with `event_date` in the next 7 days.
- Places/openings/culture: items published in the last 7 days (these don't have a clean "now" — a new restaurant opening is relevant for several days).

### Step 2 — Embed

For each fetched item, generate an embedding of `title + first ~500 words of extracted_text` using OpenAI `text-embedding-3-small`.

Cost: negligible (~$0.02 per 1M tokens; a daily run probably ~$0.01).

### Step 3 — Cluster (with 7-day rolling cache)

Maintain a SQLite database (or just JSON files in the repo) of:
- `items_seen.db`: every item from the last 7 days, with its embedding, source, title, URL, summary, and a `cluster_id`.

For each new item:
1. Compare embedding against all items in `items_seen` (cosine similarity).
2. If similarity > **0.78** with any existing item, assign it to that item's cluster. Mark as "follow-up coverage".
3. Otherwise, start a new cluster.

The 0.78 threshold is a starting point; tune after seeing real output for a week.

After clustering today's batch:
- **New clusters** (no prior items in cache): main digest content.
- **Existing clusters with new items today**: "Still in the news" section, only if there's a meaningfully new angle.
- **Items identical or near-identical to ones already shown** (similarity > 0.92 with an item already sent in a previous email): skip entirely.

### Step 4 — Synthesise per cluster

For each new cluster (and meaningfully-updated existing ones), send the items to Claude Sonnet 4.6 with a prompt like:

```
You are summarising Toulouse local news/events for a daily digest.

Cluster of {N} items covering the same topic, from sources: {sources}.

Generate:
1. A unified title (max 12 words, in French if the original is French, else English).
2. A 2-3 sentence summary of what's happening, in the language of the majority of source items.
3. If multiple sources cover it, briefly note framing differences if meaningful (e.g. "La Dépêche frames as X; Le Bonbon frames as Y"). Skip if framings are identical.

Items:
{for each item: source, title, excerpt (first 300 words)}

Return JSON: {"title": ..., "summary": ..., "framing_note": ... | null}
```

Cost estimate: ~30 clusters/day × 2k tokens each ≈ ~$0.30/day, ~$10/month. Fine.

### Step 5 — Assemble HTML email

Sections in this order:

1. **Header**: date, "Toulouse — {weekday} {date}", short tagline.
2. **À la une** (Top 3-5 clusters by source diversity) — items covered by most sources rise to the top automatically.
3. **Actualités** — news items, by recency.
4. **Événements à venir (7 jours)** — events ordered by event date.
5. **Sorties, lieux, ouvertures** — places, restaurants, openings, lifestyle.
6. **Culture** — Clutch + cultural items from other sources.
7. **Toujours d'actu** — clusters from prior days with new coverage today (small section, only if there's something).
8. **Footer**: digest stats (X items from Y sources, Z deduplicated), sources list with links to each source homepage.

Each item displays:
- **Title** (the synthesised one, not source's)
- **2-3 sentence summary**
- **"Read on:"** + source name(s) as links
- **Framing note** if present (italicised)
- **Event date** if applicable

HTML, mobile-friendly, plain styling. No tracking pixels, no images in v1 (add later if useful).

### Step 6 — Send via Resend

Resend has a free tier (3k emails/month, more than enough). Set up:
- Sender domain (use Ralph's existing domain or a `ralphward.dev` subdomain).
- DKIM/SPF for deliverability (so Gmail doesn't junk it).
- Single recipient: Ralph's Gmail.

Alternative: Gmail SMTP with an app password. Simpler setup, slightly less reliable for scheduled daily sends. Resend is the right call.

---

## Infrastructure: GitHub Actions

- **Trigger**: cron schedule, `0 5 * * *` UTC (= 07:00 Europe/Paris in summer, 06:00 in winter — accept the winter shift or adjust to handle DST with a Python check at start).
- **Runner**: `ubuntu-latest`, Python 3.12.
- **Secrets** (set in repo settings):
  - `OPENAI_API_KEY`
  - `ANTHROPIC_API_KEY`
  - `RESEND_API_KEY`
  - `RECIPIENT_EMAIL`
- **Artifacts**: persist `items_seen.db` between runs by committing it back to the repo (or use GitHub Actions cache, or a tiny S3 bucket — SQLite committed to repo is simplest for v1).

---

## Repo structure

```
toulouse-digest/
├── .github/workflows/digest.yml
├── README.md
├── pyproject.toml  (or requirements.txt)
├── src/
│   ├── main.py             # entry point, orchestrates pipeline
│   ├── fetchers/
│   │   ├── __init__.py
│   │   ├── openagenda.py
│   │   ├── la_depeche.py
│   │   ├── actu_toulouse.py
│   │   ├── lessentiel.py
│   │   ├── le_bonbon.py
│   │   ├── clutch.py
│   │   ├── toulouse_secret.py
│   │   └── toulouscope.py  # adapt Ralph's existing code
│   ├── embed.py            # OpenAI embedding wrapper
│   ├── cluster.py          # cosine sim + cache logic
│   ├── synthesise.py       # Claude wrapper for per-cluster summaries
│   ├── render.py           # HTML email template + assembly
│   ├── send.py             # Resend wrapper
│   └── cache.py            # SQLite wrapper for items_seen.db
├── data/
│   └── items_seen.db       # 7-day rolling cache
├── tests/
│   └── ...
└── prompts/
    └── synthesise.md       # the per-cluster synthesis prompt, version-controlled
```

---

## Build order (suggested for Claude Code)

1. **Scaffolding**: repo, dependencies, env vars, GitHub Actions workflow that runs a hello-world.
2. **One fetcher end-to-end**: pick La Dépêche RSS first (easiest). Fetch + extract + print.
3. **Render + send**: skip clustering, just send a plain email of La Dépêche's last 24h. Validates the delivery path.
4. **Add embeddings + cache**: SQLite, cluster within La Dépêche items only. Validates dedup logic.
5. **Add synthesise step**: Claude summarises each cluster.
6. **Add the other 7 fetchers, one at a time.** Test each in isolation before integrating.
7. **Tune cluster threshold** after a week of real output.
8. **Polish HTML email styling**.

Don't try to ship all 8 sources at once. Iterate.

---

## Known unknowns / decisions deferred

- **Cluster threshold** (0.78 is a guess — adjust after a week).
- **Image handling**: skipped in v1. If digest feels too dense, add hero images for top 3 items in v2.
- **DST handling**: GitHub Actions cron is UTC; either accept ±1h shift or compute target time in code.
- **Toulouscope scraper currency**: untested against current HTML structure as of 2026-05. Verify before relying on it.
- **Source addition / removal**: each source is a single Python module. Adding/removing later is cheap.

---

## Out of scope for v1

- Global news / tech YouTube digest (separate project — same architecture, different config).
- Spotify / music trending (use existing playlists, no project needed).
- Interest-based filtering / "highlighted for Ralph" section.
- Web archive / historical browsing of past digests.
- Mobile app, PDF export, anything beyond the daily HTML email.

---

## Cost estimate

| Item | Monthly cost |
|------|-------------|
| OpenAI embeddings (text-embedding-3-small) | ~$0.30 |
| Claude Sonnet 4.6 synthesis | ~$10 |
| Resend (free tier) | $0 |
| GitHub Actions (free for personal repos) | $0 |
| **Total** | **~$10/month** |

---

## Done criteria for v1

- Email arrives in Gmail at 07:00 daily, every day for 14 consecutive days without manual intervention.
- All 8 sources contribute items to at least one digest in that period.
- Dedup across sources visibly works (cluster sizes > 1 visible in output).
- Same item never shown two days running.
- Cluster threshold tuned based on real output.
