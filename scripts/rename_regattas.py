#!/usr/bin/env python3
"""
Give a regatta its real name, from data/regatta_renames.csv.

merge_regattas.py can fold a badly-named record into a well-named one, but
only when a well-named one already exists. Three kinds of record have no such
partner, so until now there was nothing to be done with them:

  - a name that is not a name at all. Regatta 87 was called
    "Sat 9th - Sat 16th June 2018", because the RORC page it was scraped from
    is a standings page carrying no race, so the line where the scraper looks
    for a title printed the event's date range instead. Its races know what it
    is - "IRC European Championship - Class 1" - but the regatta does not.
  - EVERY record of a series carrying a year. "2025 J70 European Championship"
    and "J70 European Championship 2026 Barcelona" are one championship, but
    folding either into the other keeps a year in the surviving name and picks
    the wrong one. One of them has to be renamed before the other can be
    folded into it.
  - a venue or sponsor baked into the name the same way.

So: rename first, fold second. This runs immediately before merge_regattas.py
for exactly that reason - a rename here is what creates the canonical record
that a row in data/regatta_merges.csv then folds the duplicates into.

Idempotent, like the other ledgers: once a record has been renamed, its old
name matches nothing and the row is skipped. That is what makes it safe in
refresh_all.py, where a re-import can re-create a wrongly-named record at any
time and this has to quietly put it right again.

WHAT IT WILL NOT DO. If the target name already belongs to a DIFFERENT record,
this refuses the rename and says so, because regattas.name is UNIQUE and the
honest description of that situation is not "rename" but "these are two
records of one regatta" - which is merge_regattas.py's job, and wants a row in
data/regatta_merges.csv instead. Silently merging here would hide a decision
nobody made.

Usage:
  python3 rename_regattas.py <db.sqlite> [--file data/regatta_renames.csv]
      [--dry-run]
"""
import csv
import argparse
import sqlite3
import pathlib


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--file", default="data/regatta_renames.csv")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    path = pathlib.Path(args.file)
    if not path.exists():
        print(f"no ledger at {path} - nothing to rename")
        return

    rows = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            old = (row.get("From") or "").strip()
            new = (row.get("To") or "").strip()
            if old and new and old != new:
                rows.append((old, new))
    if not rows:
        print("ledger is empty - nothing to rename")
        return

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    renamed = already = conflicts = 0
    for old, new in rows:
        src = cur.execute("SELECT id FROM regattas WHERE name = ?", (old,)).fetchone()
        if not src:
            # Either this was applied on an earlier run, or the record has
            # never existed. Both are quiet successes; say which, so a typo in
            # the ledger does not look like a completed rename.
            done = cur.execute("SELECT id FROM regattas WHERE name = ?", (new,)).fetchone()
            if done:
                already += 1
            else:
                print(f"  no record named {old!r}, and none named {new!r} either "
                      f"- check the ledger for a typo")
            continue

        clash = cur.execute("SELECT id FROM regattas WHERE name = ? AND id != ?",
                            (new, src[0])).fetchone()
        if clash:
            print(f"  REFUSED {old!r} -> {new!r}: regatta {clash[0]} already holds that name. "
                  f"Two records of one regatta is a merge, not a rename - put the pair in "
                  f"data/regatta_merges.csv instead.")
            conflicts += 1
            continue

        seasons = [r[0] for r in cur.execute(
            "SELECT season_year FROM events WHERE regatta_id = ? ORDER BY season_year",
            (src[0],)).fetchall()]
        if not args.dry_run:
            cur.execute("UPDATE regattas SET name = ? WHERE id = ?", (new, src[0]))
        print(f"  {old!r} -> {new!r}  (regatta {src[0]}, seasons {seasons or 'none'})")
        renamed += 1

    print(f"\n{renamed} renamed, {already} already applied, {conflicts} refused")
    if renamed:
        print("Renaming can create the name a merge row folds into, so "
              "merge_regattas.py runs next.")
    if args.dry_run:
        conn.rollback()
        print("(dry run - nothing written)")
    else:
        conn.commit()
        print("committed")
    conn.close()


if __name__ == "__main__":
    main()
