#!/usr/bin/env python3
"""
Rename RORC season-standings races that were called by their own summary line.

RORC's legacy archive publishes an overall-standings page per class per season
(slug ending -os) alongside the individual race pages. Those pages carry no
race, so the line where a race page prints its name prints a fleet summary
instead - and scrape_rorc_legacy.py took it verbatim. The result was 251 races
named things like:

    "Entries: 447\xa0\xa0\xa0\xa0\xa0Races Sailed: 12"
    "Entries: 12\xa0\xa0\xa0\xa0\xa0 Races Sailed: 2"

12,636 entries under names that are not names. The scraper now recognises the
summary line and writes "Season Standings"; this fixes the rows loaded before
it did.

The name matters beyond tidiness: it is what export_dashboard_data.py matches
on to keep these pages out of the counts. A standings page repeats every boat
that competed in the class that season, so counted as a race it adds one
duplicate entry per boat per class-season - in 2022 the IRC Overall standings
listed 392 boats and all 392 also appear in that season's race pages. Until
they are named consistently, they cannot be excluded consistently.

One-off, and safe to re-run: after the first pass nothing matches.

Usage:
  python3 rename_standings_races.py <db.sqlite> [--dry-run]
"""
import re
import argparse
import sqlite3

SUMMARY_LINE = re.compile(r"^\s*Entries:\s*\d", re.I)
NEW_NAME = "Season Standings"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT ra.id, ra.race_name, e.season_year, COUNT(re.id)
        FROM races ra
        JOIN events e ON e.id = ra.event_id
        LEFT JOIN race_entries re ON re.race_id = ra.id
        GROUP BY ra.id
    """).fetchall()
    hits = [(rid, nm, yr, n) for rid, nm, yr, n in rows if SUMMARY_LINE.match(nm or "")]
    if not hits:
        print("no summary-line race names left to fix.")
        conn.close()
        return

    by_year = {}
    for rid, nm, yr, n in hits:
        y = by_year.setdefault(yr, [0, 0])
        y[0] += 1
        y[1] += n
        if not args.dry_run:
            cur.execute("UPDATE races SET race_name = ? WHERE id = ?", (NEW_NAME, rid))

    print(f"{len(hits)} race(s) renamed to {NEW_NAME!r}, "
          f"{sum(n for _, _, _, n in hits)} entries affected:")
    for yr in sorted(by_year):
        n_races, n_entries = by_year[yr]
        print(f"    {yr}  {n_races:3} race(s)  {n_entries:5} entries")

    if args.dry_run:
        conn.rollback()
        print("(dry run - nothing written)")
    else:
        conn.commit()
        print("committed")
    conn.close()


if __name__ == "__main__":
    main()
