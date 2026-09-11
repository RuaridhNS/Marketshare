#!/usr/bin/env python3
"""
Put race dates in one format, and give events the dates they never had.

Two things were wrong with dates here, and both make a calendar impossible.

FORMAT. race_date was stored however its source wrote it: 434 rows as
"18 May 2024" (the JOG results read through a browser) and 71 as "2025-10-11"
(HalSail, Round the Island). Sorting or comparing those together is nonsense -
"18 May 2024" sorts before "2025-10-11" on a string compare and so does every
other day in May, whatever the year. Everything is ISO now, which sorts
correctly as text and is what the dashboard can hand to Date().

EVENTS HAD NO DATES AT ALL - 0 of 405 - even where their races carried one.
An event's dates are just the span of its races, so they are derived here
rather than asked for: start_date is the earliest race in the event, end_date
the latest. That is what lets a regatta appear on a calendar at all.

Only 505 of 7,062 races carry a date to begin with, so most events still end up
with none. That is a gap in the sources, not something this script can invent,
and it is reported rather than filled in.

Usage:
  python3 normalise_dates.py <db.sqlite> [--dry-run]
"""
import re
import argparse
import sqlite3

MONTHS = {m.lower(): i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}
MONTHS["sept"] = 9        # seen in the wild, and not a 3- or full-length name
MONTHS.update({m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)})


def to_iso(raw):
    """'18 May 2024' -> '2024-05-18'. Returns None if it is not a date we know."""
    s = (raw or "").strip()
    if not s:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    m = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})", s)
    if m:
        d, mon, y = m.groups()
        if mon.lower() in MONTHS:
            return f"{y}-{MONTHS[mon.lower()]:02d}-{int(d):02d}"
    m = re.fullmatch(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", s)
    if m:
        mon, d, y = m.groups()
        if mon.lower() in MONTHS:
            return f"{y}-{MONTHS[mon.lower()]:02d}-{int(d):02d}"
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
    if m:                       # day first: these are all UK sources
        d, mo, y = m.groups()
        if 1 <= int(mo) <= 12:
            return f"{y}-{int(mo):02d}-{int(d):02d}"
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    changed = unparsed = 0
    bad = []
    for rid, raw in cur.execute(
            "SELECT id, race_date FROM races WHERE IFNULL(race_date,'') <> ''").fetchall():
        iso = to_iso(raw)
        if iso is None:
            unparsed += 1
            if len(bad) < 8:
                bad.append(raw)
            continue
        if iso != raw:
            changed += 1
            if not args.dry_run:
                cur.execute("UPDATE races SET race_date = ? WHERE id = ?", (iso, rid))
    print(f"race dates: {changed} rewritten to ISO, {unparsed} not recognised")
    for b in bad:
        print(f"    unrecognised: {b!r}")

    # Event span from its own races. COALESCE so an event whose races lose
    # their dates later keeps what it had rather than being blanked.
    rows = cur.execute("""
        SELECT event_id, MIN(race_date), MAX(race_date) FROM races
        WHERE IFNULL(race_date,'') <> '' GROUP BY event_id""").fetchall()
    dated = 0
    for ev, lo, hi in rows:
        if not args.dry_run:
            cur.execute("UPDATE events SET start_date = ?, end_date = ? WHERE id = ?",
                        (lo, hi, ev))
        dated += 1
    total_events = cur.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    print(f"events: {dated} of {total_events} given a start and end date from their races")
    print(f"        {total_events - dated} still have none, because none of their races "
          f"carries a date")

    if args.dry_run:
        conn.rollback()
        print("(dry run - nothing written)")
    else:
        conn.commit()
        print("committed")
    conn.close()


if __name__ == "__main__":
    main()
