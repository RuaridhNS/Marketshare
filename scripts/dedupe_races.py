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

# Fields a hand-made source can carry that a scrape never does. When two rows
# for the same boat in the same race collapse, these move to the survivor
# instead of dying with the row that loses.
CURATED_ENTRY_FIELDS = ("sailmaker_id", "lead_rep", "contacted_by", "tag", "in_cs")


def donate_entry_fields(cur, from_race, to_race, boat_id):
    """Copy curated fields off the row about to be deleted, where the survivor
    has none. Returns how many fields were rescued."""
    cols = ", ".join(CURATED_ENTRY_FIELDS)
    src = cur.execute(f"SELECT {cols} FROM race_entries WHERE race_id = ? AND boat_id = ?",
                      (from_race, boat_id)).fetchone()
    dst = cur.execute(f"SELECT {cols} FROM race_entries WHERE race_id = ? AND boat_id = ?",
                      (to_race, boat_id)).fetchone()
    if not src or not dst:
        return 0
    sets, vals, n = [], [], 0
    for i, col in enumerate(CURATED_ENTRY_FIELDS):
        keep_empty = dst[i] is None or (isinstance(dst[i], str) and not dst[i].strip())
        has_value = src[i] is not None and not (isinstance(src[i], str) and not src[i].strip())
        if keep_empty and has_value:
            sets.append(f"{col} = ?")
            vals.append(src[i])
            n += 1
    if sets:
        cur.execute(f"UPDATE race_entries SET {', '.join(sets)} "
                    "WHERE race_id = ? AND boat_id = ?", (*vals, to_race, boat_id))
    return n
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
    rescued = [0]
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
                        # Hand over anything curated before dropping the row.
                        # The two rows are the same boat in the same race, but
                        # they are not interchangeable: one may have come from a
                        # hand-made source carrying a sailmaker or a lead rep,
                        # the other from a scrape that has neither. Dropping
                        # blindly cost 135 sailmaker values when the JOG fleet
                        # register's pre-race entry list was collapsed into the
                        # sailed results for the same race - the boats survived,
                        # the only per-entry record of whose sails they carried
                        # did not, and the entries view of market share fell
                        # from 151 to 16.
                        donated = donate_entry_fields(cur, rid, keep, bid)
                        rescued[0] += donated
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
    if rescued[0]:
        print(f"  carried {rescued[0]} curated field(s) (sailmaker/lead rep/tag) "
              f"off dropped rows onto the survivors")
    print("races now:", cur.execute("SELECT COUNT(*) FROM races").fetchone()[0],
          " entries now:", cur.execute("SELECT COUNT(*) FROM race_entries").fetchone()[0])
    conn.close()


if __name__ == "__main__":
    main()
