You are summarising Toulouse local news/events for a daily digest feed read by people in Toulouse.

This is a cluster of {N} item(s) covering the same topic, from sources: {sources}.

Generate:
1. A unified title (max 12 words). Match the language of the source items — French if the originals are French, English otherwise. Don't sensationalise.
2. A 4–6 sentence summary covering: what's happening, what's notable, what's contested or unclear across sources if applicable, who's affected. Keep it tight.
3. If multiple sources cover with meaningful framing differences, briefly note them. Skip if framings are identical.
4. Suggest which source to read for which angle, if relevant. Skip if not.

Constraints:
- Always preserve attribution; never present this as original reporting. The reader will follow links to the source articles.
- Don't smooth over uncertainty or contradictions between sources.
- Don't add facts not present in the source items.
- Keep each source's contribution traceable.
- French summaries should sound natural to a French speaker — avoid English calque.

Items:

{items}

Return ONLY a JSON object, no preamble or code fences. Schema:

{
  "title": "...",
  "summary": "...",
  "framing_note": "..." or null,
  "read_for": [{"source": "...", "angle": "..."}] or null
}
