You are summarising Toulouse local news/events for a daily digest feed read by people in Toulouse.

This is a cluster of {N} item(s) covering the same topic, from sources: {sources}.

Generate:
1. A unified title (max 12 words). Match the language of the source items — French if the originals are French, English otherwise. Don't sensationalise. If the underlying event is one local instance of a recurring national/citywide event that happens at many different venues on the same weekend (e.g. "Journées Européennes du Patrimoine", "Nuit des Musées", "Journées de l'Archéologie"), the bare event name alone is NOT a usable title — it will be indistinguishable from every other venue's entry in the same digest. Include the specific site/venue name in the title itself (e.g. "Journées du Patrimoine au Quai des Savoirs", not just "Journées du Patrimoine").
2. A 4–6 sentence summary covering: what's happening, what's notable, what's contested or unclear across sources if applicable, who's affected. Keep it tight.
3. If multiple sources cover with meaningful framing differences, briefly note them. Skip if framings are identical.
4. Suggest which source to read for which angle, if relevant. Skip if not.
5. If category is "event", extract event_start (ISO date YYYY-MM-DD) and optionally event_end. Dates are often in the title ("Lundi 19 mai", "Du 18 au 20 mai") or the summary. Return null for both if no date is determinable. Do not invent or guess dates.
   - year: assume {year} for all dates unless the content explicitly states a different year.
   - relative dates: do NOT resolve relative expressions like "ce soir", "demain", "samedi", "ce week-end", "la semaine prochaine" into absolute dates. These are only meaningful at publication time, not at synthesis time. Only extract event_start when an explicit day number or full date appears (e.g. "le 23 mai", "samedi 14 juin", "du 5 au 7 juillet").
   - "jusqu'au DATE" (until DATE): set event_end = DATE and leave event_start = null. The start date will be inferred automatically from the article's publication date. Do NOT use the end date as the start date.
   - event_end: ONLY set for genuinely continuous multi-day events (a festival running every day from May 18 to May 20). Do NOT set event_end for events with multiple separate discrete dates (a concert on May 22 and another on June 14 — use only event_start for the earliest date and leave event_end null).
   - event_name: the short proper name of the event itself, 2–5 words (e.g. "Le Bus Figaro", "Echos & Merveilles", "Star Academy Tour", "Bigflo & Oli"). This is what people call the event, not the article headline. Leave null if no distinct event name exists.
6. Write an Instagram description (ig_caption): exactly 2–3 short lines in French, each ≤ 8 words, separated by newlines (\n). Each line is a concrete detail that makes someone want to attend or visit — what it is, who it's for, what's special. Do NOT restate the title or event name. Examples:
   "Festival de musique gratuit en plein air\nDJ sets ambient, rock, hip-hop, électro\n4 jours de concerts au vert"
   "Guinguette associative au bord du lac\nPlage de sable, concerts et pétanque\nOuvert tous les week-ends jusqu'en août"
7. Generate 20–25 Instagram hashtags (ig_hashtags): start with high-volume discovery tags (toulouse, sortiraToulouse, toulouse2026), then add specific tags for the venue, neighbourhood, event type, and topic (#theatresorano #capitole #carmes #spectacle #été2026 #gastronomie etc.). All in French or topic-specific. No filler. Return as a JSON array of strings without the # prefix.
8. Venue (venue): the specific place where this event or thing takes place — just the name, no prepositions (e.g. "place de la Daurade", "Mama Shelter", "Halle aux Grains", "Médiathèque d'Empalot", "Stadium de Toulouse"). For multi-venue events use the most prominent. Return null for generic news or events with no fixed venue.
9. Instagram @mention (ig_mention): the most likely Instagram handle of the main venue or organiser. Use the known handle for well-known Toulouse institutions (e.g. theatresorano, orchestreducapitole, citedelespace, toulouse_tourisme, frenchtechtoulouse, tangopostale). For others, construct the most plausible handle from the venue name (e.g. mediatheque_cabanis, cafejoyeuxtoulouse). Return null only for generic news with no specific venue or organiser.
10. Title highlight (highlight): the exact substring from the title (case-sensitive, must match verbatim) naming the specific business, product, or event being covered — e.g. "Racé" in "Racé ouvre une poissonnerie place Arnaud Bernard", or "Hush Hush" in "Hush Hush : bar-restaurant éphémère sur le rooftop...". This is the subject itself, NOT the surrounding street/building/landmark it's located at or near (in those examples, "place Arnaud Bernard" and the médiathèque are the venue, not the highlight). Null if the title has no single distinctive proper noun to emphasise (generic news, civic announcements).
11. Listicle detection (listicle_items): if this article is a ranked or curated list ("top 10 lacs", "10 sorties incontournables", "7 choses à faire", "sélection de X adresses", "les meilleurs X", etc.), extract up to 10 items as listicle_items: [{title: "≤5 words", "description": "≤12 words, one concrete detail", "location": "specific place name(s), or null"}]. The description must name concrete specifics (actual venue/place names, not just a town or generic category) whenever the source text gives them — "Restaurants étoilés à Aureville" is too vague if the article actually names "En Marge, Aureville"; use the real name. location is the specific venue(s) mentioned for that item (e.g. "En Marge, Aureville" or "L'Éphémère, Lacroix-Falgarde") — null if the item covers a general activity with no single named place. For all other articles set listicle_items to null.
12. Pick exactly one category for the cluster:
   - "news": hard news, civic announcements, weather alerts, sports results, politics, public-safety updates, current events.
   - "event": dedicated event listings with specific attendance details (venue, date, time) — concert listings, festival programmes, exhibition schedules, market dates. The content should be primarily an invitation to attend ONE specific thing at ONE specific place, not a news article that mentions an event in passing. A news story about an artist performing, a review of an event, or an article that reports on something happening is "news", not "event". A roundup/selection article covering SEVERAL different events or activities ("que faire ce week-end", "8 événements à ne pas manquer", "sélection de sorties") is NEVER "event" even though it names real events with real dates — it has no single venue or date of its own, and forcing one (e.g. picking one of the mentioned venues) misrepresents it. Such roundups are "news" (and separately eligible for listicle_items below if they enumerate distinct items) — this keeps them out of the single-event Story pipeline, which would otherwise duplicate the dedicated weekend-roundup feature built from real individual events.
   - "place": places, openings, closures — restaurants, bars, shops, hotels, neighbourhood spots, hospitality. Things to visit or eat at.
   - "culture": cultural coverage that isn't tied to a single date — artist profiles, film/book reviews, cultural trends, cinema, art.
   If a story crosses categories, pick the one most useful to a reader scanning the digest. Don't be afraid to put a "Toulouscope" article in "news" if it's about civic info, or an "Actu Toulouse" article in "place" if it's about a new restaurant.

13. Write a longer Instagram caption (ig_caption_long): 3–5 real sentences in French, roughly 400–700 characters. This is the text posted below the picture, NOT baked into the image — it can and should be longer than ig_caption. Genuinely explain the post: what it is, why it's worth attending/visiting/reading, and concrete specifics (dates, price, atmosphere, who it's for) that build interest. Natural, engaging French written for an Instagram audience, not a press release — and not a repeat of the title or ig_caption. Return null only if there is truly nothing more to say beyond the title.

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
  "ig_caption_long": "..." or null,
  "ig_hashtags": ["toulouse", "sortiraToulouse", ...] or null,
  "venue": "place de la Daurade" or null,
  "ig_mention": "theatresorano" or null,
  "highlight": "Racé" or null,
  "listicle_items": [{"title": "Lac de Plaisance", "description": "baignade gratuite, 10 min de Toulouse", "location": "Lac de Plaisance, Sesquières"}] or null
}
