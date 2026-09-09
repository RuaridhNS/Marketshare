#!/usr/bin/env python3
"""
Drop entry-list rows for boats whose results have since arrived.

Entry lists are loaded on purpose, for races that have not been sailed yet -
they are the only way to see a fleet before the results exist. The problem is
what happens afterwards. JOG's 2026 Lonely Tower was loaded from the fleet
spreadsheet as 93 boats with status 'entered' and no date; when the race was
sailed, the results came in from the club's own pages under the sponsored name
"Lewmar Lonely Tower", 100 boats over 7 races. 91 boats are in both, counted
twice, and the event appeared in the regatta tree twice under two names.

The regatta halves of that are handled by data/regatta_merges.csv. This handles
the entries: a row from a hand-loaded entry list is superseded once the same
boat has a result-bearing row for the same organiser in the same season.

WHY SEASON AND ORGANISER, NOT THE EVENT. An entry-list row usually has no date
and no race identity - "JOG Lonely Tower" is the whole of what it says - so it
often cannot be matched to the event whose results replaced it. JOG's 2026
Cherbourg entry list is the clearest case: 34 boats covering the whole weekend,
whose results arrived as two separate races, Cowes-Cherbourg (29 boats) and
Cherbourg-Cowes (18). No single event supersedes it; the season does.

What this deliberately keeps: entry-list rows for boats with NO result that
season. Those are the ones worth having - a boat that entered and never sailed
is still fleet presence, and nothing else in the database records it.

Only hand-loaded entry lists are in scope. A scraper's 'entered' row means
something different - the boat started and the page recorded no finish - and
that is a result, not an expectation.

Usage:
  python3 drop_superseded_entries.py <db.sqlite> [--dry-run]
"""
import argparse
import sqlite3

# The hand-loaded entry lists. A scraped 'entered' row is not one of these.
ENTRY_LIST_SOURCES = ("manual:jog_fleet_combined", "entry-list")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    placeholders = ",".join("?" * len(ENTRY_LIST_SOURCES))

    # (organiser, season) pairs where a boat has a real result: a finishing
    # position, a corrected time, or a status that says it sailed.
    rows = cur.execute(f"""
        SELECT re.id, re.boat_id, re.boat_name_used, rg.name, rg.category, e.season_year
        FROM race_entries re
        JOIN races ra ON ra.id = re.race_id
        JOIN events e ON e.id = ra.event_id
        JOIN regattas rg ON rg.id = e.regatta_id
        WHERE re.source IN ({placeholders})
    """, ENTRY_LIST_SOURCES).fetchall()
    if not rows:
        print("no hand-loaded entry-list rows on file.")
        conn.close()
        return

    have_result = set()
    for boat_id, cat, yr in cur.execute(f"""
            SELECT DISTINCT re.boat_id, IFNULL(rg.category, rg.name), e.season_year
            FROM race_entries re
            JOIN races ra ON ra.id = re.race_id
            JOIN events e ON e.id = ra.event_id
            JOIN regattas rg ON rg.id = e.regatta_id
            WHERE re.source NOT IN ({placeholders})
              AND (IFNULL(re.position, '') <> '' OR IFNULL(re.corrected_time, '') <> ''
                   OR re.status IN ('finished', 'retired', 'disqualified'))
    """, ENTRY_LIST_SOURCES):
        have_result.add((boat_id, cat, yr))

    superseded, kept = [], []
    for rid, boat_id, name, rg_name, cat, yr in rows:
        key = (boat_id, cat or rg_name, yr)
        (superseded if key in have_result else kept).append((rid, name, rg_name, yr))

    if superseded and not args.dry_run:
        cur.executemany("DELETE FROM race_entries WHERE id = ?",
                        [(r[0],) for r in superseded])

    print(f"{len(superseded)} entry-list row(s) superseded by a real result "
          f"and dropped; {len(kept)} kept")
    by_reg = {}
    for _, _, rg_name, yr in superseded:
        by_reg[(yr, rg_name)] = by_reg.get((yr, rg_name), 0) + 1
    for (yr, rg_name), n in sorted(by_reg.items()):
        print(f"    {yr}  {rg_name[:44]:46} -{n}")
    if kept:
        print("  kept (no result for that boat all season - real fleet presence):")
        seen = {}
        for _, _, rg_name, yr in kept:
            seen[(yr, rg_name)] = seen.get((yr, rg_name), 0) + 1
        for (yr, rg_name), n in sorted(seen.items()):
            print(f"    {yr}  {rg_name[:44]:46} {n}")

    if args.dry_run:
        conn.rollback()
        print("(dry run - nothing written)")
    else:
        conn.commit()
        print("committed")
    conn.close()


if __name__ == "__main__":
    main()
