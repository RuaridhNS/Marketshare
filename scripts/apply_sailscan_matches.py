#!/usr/bin/env python3
"""
Fill sailmakers from North's sail-scan system.

North's scan tool holds shape measurements for sails it has analysed, so a boat
appearing in it has had North sails on it. That is a real signal and it checks
out: of the boats visible there that we ALREADY had a sailmaker for, ten out of
ten are North and none is a competitor.

It is not a market-share database. Only ~39 GBR boats are visible in it against
3,480 boats here with no sailmaker, so this closes a sliver of the gap, not the
gap.

The trap is the sail number. Numbers are reused across classes and countries,
and matching on the number alone produced four confident nonsense pairs - a
6 Metre matched to a Class40, a 90ft Hoek to an Elan 410. So a match counts
only where the name or the boat type corroborates it, and every decision,
accepted or rejected, is written down in data/sailscan_matches.csv where it can
be argued with.

Written at confidence 'inferred', not 'stated': a scan proves North worked on
the boat's sails, not that the whole inventory is North today.

Usage:
  python3 apply_sailscan_matches.py <db.sqlite> [--dry-run]
                                    [--file data/sailscan_matches.csv]
"""
import sys
import csv
import argparse
import sqlite3
import pathlib
import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_db import get_or_create_sailmaker, norm, norm_upper


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--file", default="data/sailscan_matches.csv")
    p.add_argument("--maker", default="North Sails")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    path = pathlib.Path(args.file)
    if not path.exists():
        print(f"no match file at {path}")
        return

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    today = datetime.date.today().isoformat()
    sm_id = get_or_create_sailmaker(cur, args.maker)

    applied = skipped = already = missing = rejected = 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            verdict = (norm(row.get("Verdict")) or "").lower()
            sail = norm_upper(row.get("SailNo"))
            if verdict != "accept":
                rejected += 1
                continue
            got = cur.execute("SELECT id, boat_name FROM boats WHERE sail_no = ?", (sail,)).fetchone()
            if not got:
                print(f"  {sail}: not on file")
                missing += 1
                continue
            boat_id, name = got
            have = cur.execute(
                """SELECT s.name FROM boat_sailmaker_history h
                   JOIN sailmakers s ON s.id = h.sailmaker_id
                   WHERE h.boat_id = ? AND h.confidence != 'partial'
                   ORDER BY h.effective_from DESC LIMIT 1""", (boat_id,)).fetchone()
            if have:
                # never overwrite a better-sourced answer with an inference
                print(f"  {sail} {name}: already {have[0]}, left alone")
                already += 1
                continue
            if not args.dry_run:
                cur.execute(
                    "INSERT OR REPLACE INTO boat_sailmaker_history "
                    "(boat_id, sailmaker_id, effective_from, source, confidence) "
                    "VALUES (?,?,?,'ns:sail-scan','inferred')", (boat_id, sm_id, today))
            print(f"  {sail} {name}: {args.maker} (from a sail scan)")
            applied += 1

    print(f"\n{applied} boat(s) given a sailmaker, {already} already had one, "
          f"{missing} not on file, {rejected} rejected in the file")
    if args.dry_run:
        conn.rollback()
        print("(dry run - nothing written)")
    else:
        conn.commit()
        print("committed")
    conn.close()


if __name__ == "__main__":
    main()
