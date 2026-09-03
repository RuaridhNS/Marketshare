#!/usr/bin/env python3
"""
Repair text that was read as Latin-1 when it was really UTF-8.

A French owner called RACINE comes through fine; one called BENOIT ROUSSELIN
with a circumflex does not. Somewhere upstream a byte string was decoded with
cp1252 instead of utf-8, so "BENOÎT" became "BENOÃ®T" - two characters where
one belongs. The damage is silent and it makes the name unsearchable: nobody
types "BENOÃ®T".

The repair is the inverse of the damage: encode back to the bytes cp1252 would
have produced, then decode those bytes as the UTF-8 they always were. A string
only counts as damaged if that round trip succeeds AND changes it, so text
that merely contains an accent is left alone.

Names are stored upper-cased, and the mangling happened before that, so the
repaired accent comes back lower-case in an otherwise upper-case name -
"HéMON-CAMUS". Those columns are re-upper-cased after repair.

Usage:
  python3 repair_mojibake.py <db.sqlite> [--dry-run]
"""
import argparse
import sqlite3

# Names are UNIQUE in these tables, so repairing one can collide with a row
# that was already correct - the same person on file twice, once mangled and
# once not. That is not an obstacle to the repair, it is a second thing the
# repair finds: the two rows are merged, references repointed, duplicate
# dropped.
# For each referencing table: the FK column, and the other columns that decide
# whether two rows are "the same fact about the same boat". A row that will not
# move because the surviving owner already has that exact fact is redundant and
# is dropped; anything else is reported and left alone rather than deleted on
# the assumption that it must have been a duplicate.
MERGEABLE = {
    "owners": [("boats", "current_owner_id", []),
               ("race_entries", "owner_id", ["race_id", "boat_id"]),
               ("boat_owner_history", "owner_id", ["boat_id", "effective_from"])],
    "people": [("race_crew", "person_id", ["race_id", "boat_id"])],
}

# table, column, whether the column is stored upper-cased
COLUMNS = [
    ("boats", "boat_name", True),
    ("boats", "boat_type", False),
    ("owners", "name", True),
    ("people", "name", True),
    ("regattas", "name", False),
    ("race_entries", "boat_name_used", True),
    ("race_entries", "owner_name_used", True),
    ("race_entries", "skipper_name_used", True),
]


def repaired(text, upper):
    """The true string, or None if this one was never damaged."""
    if not text:
        return None
    try:
        out = text.encode("cp1252", errors="strict").decode("utf-8", errors="strict")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return None
    if out == text:
        return None
    if upper:
        out = out.upper()
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    total = 0
    for table, col, upper in COLUMNS:
        rows = cur.execute(
            f"SELECT rowid, {col} FROM {table} "
            f"WHERE {col} LIKE '%Ã%' OR {col} LIKE '%â€%' OR {col} LIKE '%Â%'").fetchall()
        fixes = [(rid, old, repaired(old, upper)) for rid, old in rows]
        fixes = [f for f in fixes if f[2]]
        if not fixes:
            continue
        print(f"{table}.{col}: {len(fixes)} of {len(rows)} repairable")
        for rid, old, new in fixes[:5]:
            print(f"    {old!r} -> {new!r}")
        if len(fixes) > 5:
            print(f"    ... and {len(fixes) - 5} more")
        merged = 0
        refs = MERGEABLE.get(table) if col == "name" else None
        for rid, old_val, new_val in fixes:
            if refs:
                dup = cur.execute(
                    f"SELECT rowid FROM {table} WHERE {col} = ? AND rowid != ?",
                    (new_val, rid)).fetchone()
                if dup:
                    keep = dup[0]
                    if not args.dry_run:
                        stuck = 0
                        for rtable, rcol, keycols in refs:
                            cur.execute(f"UPDATE OR IGNORE {rtable} SET {rcol} = ? WHERE {rcol} = ?",
                                        (keep, rid))
                            # whatever would not move collided with an existing
                            # row; drop it only where that row demonstrably
                            # holds the same fact
                            left = cur.execute(
                                f"SELECT rowid, {', '.join(keycols) or '1'} FROM {rtable} "
                                f"WHERE {rcol} = ?", (rid,)).fetchall()
                            for row in left:
                                if keycols:
                                    where = " AND ".join(f"{c} IS ?" for c in keycols)
                                    twin = cur.execute(
                                        f"SELECT 1 FROM {rtable} WHERE {rcol} = ? AND {where}",
                                        (keep, *row[1:])).fetchone()
                                else:
                                    twin = None
                                if twin:
                                    cur.execute(f"DELETE FROM {rtable} WHERE rowid = ?", (row[0],))
                                else:
                                    stuck += 1
                        if stuck:
                            print(f"    {stuck} row(s) could not be moved and were NOT deleted; "
                                  f"owner {old_val!r} kept")
                            continue
                        cur.execute(f"DELETE FROM {table} WHERE rowid = ?", (rid,))
                    print(f"    merged duplicate {old_val!r} into existing {new_val!r}")
                    merged += 1
                    continue
            if not args.dry_run:
                cur.execute(f"UPDATE {table} SET {col} = ? WHERE rowid = ?", (new_val, rid))
        if merged:
            print(f"    ({merged} merged into rows that were already correct)")
        total += len(fixes)

    print(f"\n{total} value(s) repaired")
    if args.dry_run:
        conn.rollback()
        print("(dry run - nothing written)")
    else:
        conn.commit()
        print("committed")
    conn.close()


if __name__ == "__main__":
    main()
