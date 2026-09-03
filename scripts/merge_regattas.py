#!/usr/bin/env python3
"""
Fold one regatta record into another, when both are the same real event.

consolidate_regattas.py pairs records whose NAMES are near-identical. This is
for the ones whose names are not: "RORC Cervantes Trophy (Offshore)" and "RORC
Cervantes Trophy Race" are one race, and no rule about suffixes will see that.
The pairs live in a file so each one is a decision somebody made and can
revisit, rather than a regex nobody can argue with.

What moves: every season of the folded regatta joins the surviving one. Where
both hold the same season, the two events become one and their races are
merged; dedupe_races.py then collapses any race that now exists twice under
the same (event, race name, class). Reported entry counts move too, since they
describe the event rather than the record it was filed under.

Nothing is thrown away. If both records hold the same boat in the same race,
the entry-level UNIQUE constraint keeps one; everything else survives the move.

Usage:
  python3 merge_regattas.py <db.sqlite> --keep "<name>" --fold "<name>" [--dry-run]
  python3 merge_regattas.py <db.sqlite> --file data/regatta_merges.csv [--dry-run]
"""
import csv
import argparse
import sqlite3
import pathlib


def merge(cur, keep_name, fold_name, dry_run):
    keep = cur.execute("SELECT id FROM regattas WHERE name = ?", (keep_name,)).fetchone()
    fold = cur.execute("SELECT id FROM regattas WHERE name = ?", (fold_name,)).fetchone()
    if not keep or not fold:
        print(f"  skip: {'keep' if not keep else 'fold'} regatta not found "
              f"({keep_name!r} / {fold_name!r})")
        return 0
    keep_id, fold_id = keep[0], fold[0]
    if keep_id == fold_id:
        return 0

    moved_events = merged_events = moved_races = moved_counts = 0
    rows = cur.execute("SELECT id, season_year FROM events WHERE regatta_id = ?",
                       (fold_id,)).fetchall()
    for ev_id, yr in rows:
        twin = cur.execute(
            "SELECT id FROM events WHERE regatta_id = ? AND season_year IS ?",
            (keep_id, yr)).fetchone()
        if twin:
            # same season on both records: races and counts move across, the
            # now-empty event goes
            target = twin[0]
            n = cur.execute("SELECT COUNT(*) FROM races WHERE event_id = ?", (ev_id,)).fetchone()[0]
            m = cur.execute("SELECT COUNT(*) FROM event_class_counts WHERE event_id = ?",
                            (ev_id,)).fetchone()[0]
            if not dry_run:
                cur.execute("UPDATE races SET event_id = ? WHERE event_id = ?", (target, ev_id))
                cur.execute("UPDATE OR IGNORE event_class_counts SET event_id = ? WHERE event_id = ?",
                            (target, ev_id))
                cur.execute("DELETE FROM event_class_counts WHERE event_id = ?", (ev_id,))
                cur.execute("DELETE FROM events WHERE id = ?", (ev_id,))
            moved_races += n
            moved_counts += m
            merged_events += 1
        else:
            if not dry_run:
                cur.execute("UPDATE events SET regatta_id = ? WHERE id = ?", (keep_id, ev_id))
            moved_events += 1

    if not dry_run:
        cur.execute("DELETE FROM regattas WHERE id = ?", (fold_id,))
    print(f"  {fold_name!r} -> {keep_name!r}: {moved_events} season(s) moved, "
          f"{merged_events} merged into an existing season "
          f"({moved_races} races, {moved_counts} class counts)")
    return 1


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--keep")
    p.add_argument("--fold")
    p.add_argument("--file")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    pairs = []
    if args.file:
        path = pathlib.Path(args.file)
        if path.exists():
            with open(path, newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    k, d = (row.get("Keep") or "").strip(), (row.get("Fold") or "").strip()
                    if k and d:
                        pairs.append((k, d))
    if args.keep and args.fold:
        pairs.append((args.keep, args.fold))
    if not pairs:
        print("nothing to merge")
        return

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    n = sum(merge(cur, k, d, args.dry_run) for k, d in pairs)
    print(f"\n{n} regatta(s) folded")
    if args.dry_run:
        conn.rollback()
        print("(dry run - nothing written)")
    else:
        conn.commit()
        print("committed")
        print("NOW RUN dedupe_races.py: a season held by both records can now "
              "carry the same race twice.")
    conn.close()


if __name__ == "__main__":
    main()
