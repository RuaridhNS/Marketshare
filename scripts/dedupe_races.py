#!/usr/bin/env python3
"""
Remove duplicate race rows created by re-running scrapers.

create_race() used to insert unconditionally, so each re-run of a scraper minted
a fresh set of races and loaded its entries alongside the originals rather than
on top of them. (race_entries has UNIQUE(race_id, boat_id), which dedupes
entries WITHIN a race - but a second race row sidesteps that entirely, which is
why the duplication went unnoticed.)

Only genuinely duplicated races are merged: those sharing an (event_id,
race_name) AND whose entries carry exactly the same set of class labels. Groups
where the class sets differ are left alone - RORC publishes the same race under
"IRC Overall" and again under each class, and those are legitimately separate
result sets, not duplicates.

Entries move to the earliest race id in each group; where the keeper already has
that boat, the duplicate entry is dropped (same boat, same race, same class).

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

    groups = defaultdict(list)
    for ev, nm, rid in cur.execute(
            "SELECT event_id, COALESCE(race_name,''), id FROM races ORDER BY id"):
        groups[(ev, nm)].append(rid)

    merged_races = moved = dropped = skipped_groups = 0
    for (ev, nm), ids in groups.items():
        if len(ids) < 2:
            continue
        classsets = {}
        for rid in ids:
            classsets[rid] = frozenset(
                r[0] or "" for r in cur.execute(
                    "SELECT DISTINCT class FROM race_entries WHERE race_id = ?", (rid,)))
        if len(set(classsets.values())) != 1:
            skipped_groups += 1          # real class split - leave it be
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
