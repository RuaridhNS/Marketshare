#!/usr/bin/env python3
"""
Correct ownership that was inferred from a results page naming the wrong person.

Ownership is inferred from race entries: whoever a results page prints in the
owner column becomes the owner for that season. Results pages do not reliably
print the OWNER - they print whoever entered the boat, which is often the boat
captain, the programme manager, or a co-skipper. The owner then appears to
change for a season and change back.

NIFTY (GBR316X) is the worked example. At Cowes Week 2021 five of six entries
named Ashley Bower as both owner and skipper and one named Roger Bowden, so
the importer handed Bower the boat for 2021 - between two spells of Bowden
either side. Bowden has always owned it; Bower is the project manager.

Corrections live in data/owner_corrections.csv and are re-applied on every run,
so a fresh scrape of the same results page cannot quietly undo them. Each row
says who was wrongly recorded, who the real owner is, and - this is the point -
what the wrongly-recorded person actually IS. Deleting the bad row would throw
away a true fact; a programme manager is worth knowing, and is often the person
a sales conversation should start with.

The entries themselves keep owner_name_used exactly as the source printed it.
That is the evidence, and overwriting it would hide why the mistake happened.
Only the resolved owner_id is repointed.

Usage:
  python3 apply_owner_corrections.py <db.sqlite> [--dry-run]
                                     [--file data/owner_corrections.csv]
"""
import sys
import csv
import argparse
import sqlite3
import datetime
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_db import get_or_create_owner, norm

ROLES = {"programme_manager", "boat_captain"}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--file", default="data/owner_corrections.csv")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    path = pathlib.Path(args.file)
    if not path.exists():
        print(f"no corrections file at {path}")
        return

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    now = datetime.datetime.now().isoformat()

    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    for row in rows:
        sail = norm(row.get("SailNo"))
        wrong = norm(row.get("WrongOwner"))
        right = norm(row.get("CorrectOwner"))
        role = (norm(row.get("ReassignWrongAs")) or "").lower()
        if not (sail and wrong and right):
            continue
        if role and role not in ROLES:
            print(f"  {sail}: unknown role {role!r}, skipping the reassignment")
            role = ""

        got = cur.execute("SELECT id, boat_name FROM boats WHERE sail_no = ?", (sail,)).fetchone()
        if not got:
            print(f"  {sail}: no such boat")
            continue
        boat_id, boat_name = got

        wrong_id = cur.execute("SELECT id FROM owners WHERE name = ?", (wrong,)).fetchone()
        right_id = get_or_create_owner(cur, right)
        print(f"\n{boat_name} ({sail})")

        if wrong_id:
            wrong_id = wrong_id[0]
            spans = cur.execute(
                "SELECT effective_from, effective_to FROM boat_owner_history "
                "WHERE boat_id = ? AND owner_id = ?", (boat_id, wrong_id)).fetchall()
            for fr, to in spans:
                print(f"  removing wrongly-inferred ownership {fr}-{to or 'present'}: {wrong}")
            if not args.dry_run:
                cur.execute("DELETE FROM boat_owner_history WHERE boat_id = ? AND owner_id = ?",
                            (boat_id, wrong_id))
            # the real owner absorbs the span the wrong one occupied
            earliest = min([s[0] for s in spans], default=None)
            if earliest:
                cur_from = cur.execute(
                    "SELECT MIN(effective_from) FROM boat_owner_history "
                    "WHERE boat_id = ? AND owner_id = ?", (boat_id, right_id)).fetchone()[0]
                if cur_from is None:
                    if not args.dry_run:
                        cur.execute(
                            "INSERT INTO boat_owner_history (boat_id, owner_id, effective_from, "
                            "source, confidence) VALUES (?,?,?,'manual:owner-correction','manual')",
                            (boat_id, right_id, earliest))
                    print(f"  {right} recorded from {earliest}")
                elif str(earliest) < str(cur_from):
                    if not args.dry_run:
                        cur.execute(
                            "UPDATE boat_owner_history SET effective_from = ?, "
                            "source = 'manual:owner-correction', confidence = 'manual' "
                            "WHERE boat_id = ? AND owner_id = ? AND effective_from = ?",
                            (earliest, boat_id, right_id, cur_from))
                    print(f"  {right} extended back {cur_from} -> {earliest}")

            # the entries keep the printed name; only the resolved link moves
            n = cur.execute("SELECT COUNT(*) FROM race_entries WHERE boat_id = ? AND owner_id = ?",
                            (boat_id, wrong_id)).fetchone()[0]
            if n:
                if not args.dry_run:
                    cur.execute("UPDATE race_entries SET owner_id = ? WHERE boat_id = ? AND owner_id = ?",
                                (right_id, boat_id, wrong_id))
                print(f"  {n} entr{'y' if n == 1 else 'ies'} repointed to {right} "
                      f"(owner_name_used left as printed)")
        else:
            print(f"  {wrong} not on file as an owner - nothing to remove")

        if not args.dry_run:
            cur.execute("UPDATE boats SET current_owner_id = ?, updated_at = ? WHERE id = ?",
                        (right_id, now, boat_id))
        print(f"  current owner set to {right}")

        # the wrongly-recorded person is not noise - record what they actually are
        if role:
            if not args.dry_run:
                cur.execute(
                    f"INSERT INTO boat_crm (boat_id, {role}) VALUES (?,?) "
                    f"ON CONFLICT(boat_id) DO UPDATE SET {role}=excluded.{role}, last_updated=?",
                    (boat_id, wrong, now))
            print(f"  {wrong} recorded as {role.replace('_', ' ')}")

    if args.dry_run:
        conn.rollback()
        print("\n(dry run - nothing written)")
    else:
        conn.commit()
        print("\ncommitted")
    conn.close()


if __name__ == "__main__":
    main()
