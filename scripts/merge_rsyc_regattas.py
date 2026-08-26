#!/usr/bin/env python3
"""
Merge the duplicate Royal Southern regatta records.

The same four real regattas ended up stored twice:
  - "RSYC <Month> Regatta"           - aggregate class counts imported from the
                                       IRC Solent Report workbook (2018-2025),
                                       no boat-level entries at all
  - "Royal Southern <Month> Regatta" - boat-level race entries scraped from
                                       scm.royal-southern.co.uk (2023-2026),
                                       no class counts at all

They are exactly complementary, so merging gives each regatta its full
history: aggregate totals back to 2018 plus boat-level detail from 2023 on.
The "Royal Southern ..." record is kept as the survivor (better name, and it
is what the scraper writes to, so future runs land in the right place).

Per year:
  - if the survivor already has an event for that year, move the RSYC event's
    event_class_counts rows onto it (back-filling any null event metadata),
    then delete the now-empty RSYC event
  - otherwise just re-point the RSYC event at the survivor regatta

Safe to re-run: once the RSYC regattas are gone there is nothing left to move.

Usage:
  python3 merge_rsyc_regattas.py <db.sqlite> [--dry-run]
"""
import sys
import argparse
import sqlite3

MONTHS = ("May", "June", "July", "September")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    moved_cc = moved_ev = dropped_ev = dropped_reg = 0

    for month in MONTHS:
        old_name, new_name = f"RSYC {month} Regatta", f"Royal Southern {month} Regatta"
        old = cur.execute("SELECT id FROM regattas WHERE name = ?", (old_name,)).fetchone()
        new = cur.execute("SELECT id FROM regattas WHERE name = ?", (new_name,)).fetchone()
        if not old:
            print(f"  {old_name}: already merged / not present")
            continue
        if not new:
            # nothing to merge into - just rename in place
            print(f"  {old_name}: no '{new_name}' to merge into; renaming")
            if not args.dry_run:
                cur.execute("UPDATE regattas SET name = ? WHERE id = ?", (new_name, old[0]))
            continue
        old_id, new_id = old[0], new[0]

        events = cur.execute(
            "SELECT id, season_year FROM events WHERE regatta_id = ? ORDER BY season_year",
            (old_id,)).fetchall()
        for old_ev, year in events:
            tgt = cur.execute(
                "SELECT id FROM events WHERE regatta_id = ? AND season_year = ?",
                (new_id, year)).fetchone()
            if tgt:
                tgt_ev = tgt[0]
                n = cur.execute("SELECT COUNT(*) FROM event_class_counts WHERE event_id = ?",
                                (old_ev,)).fetchone()[0]
                if not args.dry_run:
                    cur.execute("UPDATE event_class_counts SET event_id = ? WHERE event_id = ?",
                                (tgt_ev, old_ev))
                    # keep any metadata the scraped event lacks
                    cur.execute("""
                        UPDATE events SET
                          start_date = COALESCE(start_date, (SELECT start_date FROM events WHERE id = ?)),
                          end_date   = COALESCE(end_date,   (SELECT end_date   FROM events WHERE id = ?)),
                          source_url = COALESCE(source_url, (SELECT source_url FROM events WHERE id = ?)),
                          notes      = COALESCE(notes,      (SELECT notes      FROM events WHERE id = ?))
                        WHERE id = ?""", (old_ev, old_ev, old_ev, old_ev, tgt_ev))
                    cur.execute("DELETE FROM events WHERE id = ?", (old_ev,))
                moved_cc += n
                dropped_ev += 1
                print(f"    {month} {year}: moved {n} class-count row(s) into existing event, dropped dup event")
            else:
                if not args.dry_run:
                    cur.execute("UPDATE events SET regatta_id = ? WHERE id = ?", (new_id, old_ev))
                moved_ev += 1
                print(f"    {month} {year}: re-pointed event at '{new_name}'")

        left = cur.execute("SELECT COUNT(*) FROM events WHERE regatta_id = ?", (old_id,)).fetchone()[0]
        if args.dry_run or left == 0:
            if not args.dry_run:
                cur.execute("DELETE FROM regattas WHERE id = ?", (old_id,))
            dropped_reg += 1
            print(f"  {old_name}: merged into {new_name}, regatta removed")
        else:
            print(f"  {old_name}: {left} event(s) still attached - NOT removing")

    if args.dry_run:
        conn.rollback()
        print("\n(dry run - nothing written)")
    else:
        conn.commit()
    print(f"\nMoved {moved_cc} class-count row(s); re-pointed {moved_ev} event(s); "
          f"dropped {dropped_ev} duplicate event(s) and {dropped_reg} regatta(s).")
    conn.close()


if __name__ == "__main__":
    main()
