You are summarising Toulouse local news/events for a daily digest feed read by people in Toulouse.

This is a cluster of {N} item(s) covering the same topic, from sources: {sources}.

Generate:
1. A unified title (max 12 words). Match the language of the source items — French if the originals are French, English otherwise. Don't sensationalise.
2. A 4–6 sentence summary covering: what's happening, what's notable, what's contested or unclear across sources if applicable, who's affected. Keep it tight.
3. If multiple sources cover with meaningful framing differences, briefly note them. Skip if framings are identical.
4. Suggest which source to read for which angle, if relevant. Skip if not.
5. If category is "event", extract event_start (ISO date YYYY-MM-DD) and optionally event_end. Dates are often in the title ("Lundi 19 mai", "Du 18 au 20 mai") or the summary. Return null for both if no date is determinable. Do not invent or guess dates.
   - year: assume {year} for all dates unless the content explicitly states a different year.
   - relative dates: do NOT resolve relative expressions like "ce soir", "demain", "samedi", "ce week-end", "la semaine prochaine" into absolute dates. These are only meaningful at publication time, not at synthesis time. Only extract event_start when an explicit day number or full date appears (e.g. "le 23 mai", "samedi 14 juin", "du 5 au 7 juillet").
   - "jusqu'au DATE" (until DATE): set event_end = DATE and leave event_start = null. The start date will be inferred automatically from the article's publication date. Do NOT use the end date as the start date.
   - event_end: ONLY set for genuinely continuous multi-day events (a festival running every day from May 18 to May 20). Do NOT set event_end for events with multiple separate discrete dates (a concert on May 22 and another on June 14 — use only event_start for the earliest date and leave event_end null).
   - event_name: the short proper name of the event itself, 2–5 words (e.g. "Le Bus Figaro", "Echos & Merveilles", "Star Academy Tour", "Bigflo & Oli"). This is what people call the event, not the article headline. Leave null if no distinct event name exists.
6. Write a short Instagram caption (ig_caption): one punchy sentence, max 15 words, in French. It must say WHAT the thing is and WHERE or WHO, not just restate the title. Focus on the most concrete detail that would make someone want to attend or visit. Examples: "Guinguette en bord de Garonne, plage de sable et concerts le dimanche." / "Expo gratuite sur la faune toulousaine jusqu'en septembre aux Jacobins."
7. Generate 5–7 Instagram hashtags (ig_hashtags): mix of high-volume discovery tags (#toulouse #sortiraToulouse) and specific tags relevant to this content (#guinguette #boisdesbordes #été2026 #gastronomie etc.). All in French or topic-specific. No generic filler. Return as a JSON array of strings without the # prefix.
8. Pick exactly one category for the cluster:
   - "news": hard news, civic announcements, weather alerts, sports results, politics, public-safety updates, current events.
   - "event": dedicated event listings with specific attendance details (venue, date, time) — concert listings, festival programmes, exhibition schedules, market dates. The content should be primarily an invitation to attend, not a news article that mentions an event in passing. A news story about an artist performing, a review of an event, or an article that reports on something happening is "news", not "event".
   - "place": places, openings, closures — restaurants, bars, shops, hotels, neighbourhood spots, hospitality. Things to visit or eat at.
   - "culture": cultural coverage that isn't tied to a single date — artist profiles, film/book reviews, cultural trends, cinema, art.
   If a story crosses categories, pick the one most useful to a reader scanning the digest. Don't be afraid to put a "Toulouscope" article in "news" if it's about civic info, or an "Actu Toulouse" article in "place" if it's about a new restaurant.

Constraints:
- Always preserve attribution; never present this as original reporting. The reader will follow links to the source articles.
- Don't smooth over uncertainty or contradictions between sources.
- Don't add facts not present in the source items.
- Keep each source's contribution traceable.
- French summaries should sound natural to a French speaker — avoid English calque.
- If the excerpt is a newsletter teaser, paywall stub, or otherwise too thin to write a meaningful 4-sentence summary without inventing context, return `"skip": true` and leave title/summary/category null. Do NOT write meta-commentary explaining what you couldn't determine.

Items:

{items}

Return ONLY a JSON object, no preamble or code fences. Schema:

{
  "skip": false,
  "title": "...",
  "summary": "...",
  "framing_note": "..." or null,
  "read_for": [{"source": "...", "angle": "..."}] or null,
  "category": "news" | "event" | "place" | "culture",
  "event_start": "YYYY-MM-DD" or null,
  "event_end": "YYYY-MM-DD" or null,
  "event_name": "..." or null,
  "ig_caption": "..." or null,
  "ig_hashtags": ["toulouse", "sortiraToulouse", ...] or null
}
