#!/usr/bin/env python3
"""
Fold one boat record into another, or rescue entries filed onto the wrong boat.

Cape 31 sail numbers carry an X - GBR3113X, GBR314X, GBR3110X - and the Royal
Southern June Regatta publishes them without it. Two different things then
happen, and they need different treatment:

  A PHANTOM. Nothing else owns the bare number, so a second boat record is
  created. GBR3110 and GBR314R are JUBILEE and KATABATIC a second time, same
  owner, same class. Fold the whole record in.

  A COLLISION. Another boat already owns the bare number, so the entries land
  on IT. GBR3113 is ECLIPSE, a 0.924-rated boat that raced IRC 4 in 2018; six
  2026 entries for SWIFT HALF, a Cape 31, were filed onto it. Folding the whole
  record would merge two genuinely different boats. Only the wrongly-filed
  entries move, named by the boat name they were recorded under.

That is what OnlyNamed is for: blank folds the whole record, a name moves just
the entries recorded under it and leaves the rest where they are.

Decisions live in data/boat_merges.csv. Run --dry-run first; it prints what
each row would move before anything is written.

Usage:
  python3 merge_boats.py <db.sqlite> [--file data/boat_merges.csv] [--dry-run]
"""
import csv
import argparse
import sqlite3
import pathlib

# tables that point at a boat, and the columns that make a row unique there
CARRIED = [("race_entries", "boat_id", ["race_id"]),
           ("boat_owner_history", "boat_id", ["owner_id", "effective_from"]),
           ("boat_sailmaker_history", "boat_id", ["sailmaker_id", "effective_from"]),
           ("boat_name_history", "boat_id", ["name", "effective_from"]),
           ("boat_sail_aliases", "boat_id", ["alias_sail_no"])]


def table_exists(cur, name):
    return cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                       (name,)).fetchone() is not None


def move_entries(cur, src, dst, only_named, dry):
    """Move race entries, skipping any the destination already has for that race."""
    rows = cur.execute(
        "SELECT id, race_id FROM race_entries WHERE boat_id = ?" +
        (" AND UPPER(boat_name_used) = UPPER(?)" if only_named else ""),
        (src, only_named) if only_named else (src,)).fetchall()
    moved = dropped = 0
    for eid, race_id in rows:
        clash = cur.execute("SELECT 1 FROM race_entries WHERE race_id = ? AND boat_id = ?",
                            (race_id, dst)).fetchone()
        if clash:
            if not dry:
                cur.execute("DELETE FROM race_entries WHERE id = ?", (eid,))
            dropped += 1
        else:
            if not dry:
                cur.execute("UPDATE race_entries SET boat_id = ? WHERE id = ?", (dst, eid))
            moved += 1
    return moved, dropped


def fold(cur, keep_sail, fold_sail, only_named, dry):
    k = cur.execute("SELECT id, boat_name FROM boats WHERE sail_no = ?", (keep_sail,)).fetchone()
    f = cur.execute("SELECT id, boat_name FROM boats WHERE sail_no = ?", (fold_sail,)).fetchone()
    if not k or not f or k[0] == f[0]:
        print(f"  skip {fold_sail!r} -> {keep_sail!r}: not found or same record")
        return 0
    dst, src = k[0], f[0]

    moved, dropped = move_entries(cur, src, dst, only_named, dry)
    note = f" (only entries named {only_named!r})" if only_named else ""
    print(f"  {fold_sail} {f[1]!r} -> {keep_sail} {k[1]!r}{note}: "
          f"{moved} entr{'y' if moved == 1 else 'ies'} moved, {dropped} already present")

    if only_named:
        left = cur.execute("SELECT COUNT(*) FROM race_entries WHERE boat_id = ?", (src,)).fetchone()[0]
        print(f"    {fold_sail} keeps its own {left} entr{'y' if left == 1 else 'ies'}")
        return 1

    # whole-record fold: carry the history across, then drop the empty record
    for table, col, keys in CARRIED[1:]:
        if not table_exists(cur, table):
            continue
        if not dry:
            cur.execute(f"UPDATE OR IGNORE {table} SET {col} = ? WHERE {col} = ?", (dst, src))
            cur.execute(f"DELETE FROM {table} WHERE {col} = ?", (src,))
    if not dry:
        if table_exists(cur, "boat_crm"):
            cur.execute("DELETE FROM boat_crm WHERE boat_id = ?", (src,))
        # keep the fuller sail number findable
        if table_exists(cur, "boat_sail_aliases"):
            cur.execute("INSERT OR IGNORE INTO boat_sail_aliases (boat_id, alias_sail_no) VALUES (?,?)",
                        (dst, fold_sail))
        cur.execute("DELETE FROM boats WHERE id = ?", (src,))
    print(f"    {fold_sail} record removed; its sail number kept as an alias of {keep_sail}")
    return 1


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--file", default="data/boat_merges.csv")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    path = pathlib.Path(args.file)
    if not path.exists():
        print(f"no decisions file at {path}")
        return
    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    n = 0
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            keep = (row.get("Keep") or "").strip()
            fld = (row.get("Fold") or "").strip()
            only = (row.get("OnlyNamed") or "").strip()
            if keep and fld:
                n += fold(cur, keep, fld, only, args.dry_run)
    print(f"\n{n} record(s) processed")
    if args.dry_run:
        conn.rollback()
        print("(dry run - nothing written)")
    else:
        conn.commit()
        print("committed")
    conn.close()


if __name__ == "__main__":
    main()
