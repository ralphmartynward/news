"""Generate docs/calendar.ics from the DB. Run from the project root."""
import sqlite3
from datetime import date, timedelta
from pathlib import Path


def ics_escape(s: str) -> str:
    return (
        (s or "")
        .replace("\\", "\\\\")
        .replace(";", r"\;")
        .replace(",", r"\,")
        .replace("\n", r"\n")
        .replace("\r", "")
    )


def ics_fold(line: str) -> str:
    """Fold long lines at 75 octets per RFC 5545."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    result = []
    buf = b""
    for char in line:
        c = char.encode("utf-8")
        if len(buf) + len(c) > 75:
            result.append(buf.decode("utf-8"))
            buf = b" " + c
        else:
            buf += c
    if buf:
        result.append(buf.decode("utf-8"))
    return "\r\n".join(result)


def build_ics(rows: list[dict]) -> str:
    UTM = "?utm_source=ical&utm_medium=calendar&utm_campaign=toulouse-agenda"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//La Ville Rose News//Agenda Toulouse//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Agenda Toulouse — La Ville Rose",
        "X-WR-CALDESC:Événements toulousains agrégés chaque matin",
        "X-WR-TIMEZONE:Europe/Paris",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
    ]

    for r in rows:
        start = (r.get("event_start") or "").strip()
        if not start or len(start) < 10:
            continue
        try:
            start_dt = date.fromisoformat(start[:10])
        except ValueError:
            continue
        end_str = (r.get("event_end") or "").strip()
        try:
            end_dt = date.fromisoformat(end_str[:10]) if end_str and len(end_str) >= 10 else start_dt
        except ValueError:
            end_dt = start_dt
        dtend_dt = end_dt + timedelta(days=1)

        name = (r.get("event_name") or r.get("title") or "").strip()
        summary_text = (r.get("summary") or "").strip()[:500]
        url = (r.get("primary_url") or "").strip()
        uid = (r.get("cluster_id") or url) + "@news.lavillerose.com"

        desc = ics_escape(summary_text)
        if url:
            sep = r"\n\n" if desc else ""
            desc += sep + r"Source\: " + ics_escape(url)

        lines += [
            "BEGIN:VEVENT",
            ics_fold("UID:" + uid),
            "DTSTART;VALUE=DATE:" + start_dt.strftime("%Y%m%d"),
            "DTEND;VALUE=DATE:" + dtend_dt.strftime("%Y%m%d"),
            ics_fold("SUMMARY:" + ics_escape(name)),
            ics_fold("DESCRIPTION:" + desc),
        ]
        if url:
            lines.append(ics_fold("URL:" + url + UTM))
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def main() -> None:
    conn = sqlite3.connect("data/items_seen.db")
    conn.row_factory = sqlite3.Row
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT cluster_id, event_start, event_end, event_name, title,
                   summary, primary_url
            FROM clusters
            WHERE event_start IS NOT NULL AND event_start != ''
              AND primary_url IS NOT NULL AND primary_url != ''
            ORDER BY event_start
            """
        ).fetchall()
    ]
    conn.close()
    print(f"{len(rows)} events found")

    content = build_ics(rows)
    out = Path("docs/calendar.ics")
    out.write_bytes(content.encode("utf-8"))
    print(f"Wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
