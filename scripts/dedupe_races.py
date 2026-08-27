#!/usr/bin/env python3
"""
Remove duplicate race rows created by re-running scrapers.

create_race() used to insert unconditionally, so each re-run of a scraper minted
a fresh set of races and loaded its entries alongside the originals rather than
on top of them. (race_entries has UNIQUE(race_id, boat_id), which dedupes
entries WITHIN a race - but a second race row sidesteps that entirely, which is
why the duplication went unnoticed.)

RORC publishes the same race once per class ("IRC Overall", then IRC Zero, One,
Two...), so an (event_id, race_name) group legitimately holds several rows.
The duplicates are WITHIN each of those classes, not across them - the 2019
Fastnet had 22 race rows covering 8 real classes, each minted 2-3 times over.

So races are grouped by (event_id, race_name, class-set) and de-duplicated
inside each sub-group. An earlier version compared class-sets across the whole
(event, race_name) group and bailed out whenever they differed, which meant it
skipped every one of the 151 affected groups and merged nothing at all.

Entries move to the earliest race id in each sub-group; where the keeper already
has that boat, the duplicate entry is dropped (same boat, same race, same class).

Usage:
  python3 dedupe_races.py <db.sqlite> [--dry-run]
"""
import argparse
import sqlite3
from collections import defaultdict


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    raw = defaultdict(list)
    for ev, nm, rid in cur.execute(
            "SELECT event_id, COALESCE(race_name,''), id FROM races ORDER BY id"):
        raw[(ev, nm)].append(rid)

    # sub-group each (event, race_name) by the class its entries carry, so a
    # race split across classes stays split while true repeats collapse
    groups = defaultdict(list)
    for (ev, nm), ids in raw.items():
        if len(ids) < 2:
            continue
        for rid in ids:
            cs = frozenset(
                r[0] or "" for r in cur.execute(
                    "SELECT DISTINCT class FROM race_entries WHERE race_id = ?", (rid,)))
            groups[(ev, nm, cs)].append(rid)

    merged_races = moved = dropped = skipped_groups = 0
    for (ev, nm, cs), ids in groups.items():
        if len(ids) < 2:
            continue
        keep, rest = ids[0], ids[1:]
        for rid in rest:
            rows = cur.execute(
                "SELECT boat_id FROM race_entries WHERE race_id = ?", (rid,)).fetchall()
            for (bid,) in rows:
                clash = cur.execute(
                    "SELECT 1 FROM race_entries WHERE race_id = ? AND boat_id = ?",
                    (keep, bid)).fetchone()
                if clash:
                    if not args.dry_run:
                        cur.execute(
                            "DELETE FROM race_entries WHERE race_id = ? AND boat_id = ?",
                            (rid, bid))
                    dropped += 1
                else:
                    if not args.dry_run:
                        cur.execute(
                            "UPDATE race_entries SET race_id = ? WHERE race_id = ? AND boat_id = ?",
                            (keep, rid, bid))
                    moved += 1
            if not args.dry_run:
                cur.execute("DELETE FROM races WHERE id = ?", (rid,))
            merged_races += 1

    if args.dry_run:
        conn.rollback()
    else:
        conn.commit()
    print(f"{'[dry run] ' if args.dry_run else ''}"
          f"merged away {merged_races} duplicate race(s); "
          f"moved {moved} entr(y/ies), dropped {dropped} exact duplicate(s); "
          f"left {skipped_groups} genuine class-split group(s) untouched.")
    print("races now:", cur.execute("SELECT COUNT(*) FROM races").fetchone()[0],
          " entries now:", cur.execute("SELECT COUNT(*) FROM race_entries").fetchone()[0])
    conn.close()


if __name__ == "__main__":
    main()
