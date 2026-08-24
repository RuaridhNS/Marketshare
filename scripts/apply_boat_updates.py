#!/usr/bin/env python3
"""
Apply boat-level edits (sailmaker / owner / IRC TCC) made in the dashboard
back into the database. The dashboard can't write to the DB itself (it's a
static file) - instead it lets you make edits in the browser, then export
them as a CSV in this shape:

    SailNo,BoatName,NewSailmaker,NewOwner,NewTCC,Notes

Any of NewSailmaker/NewOwner/NewTCC may be blank (= no change to that field).
One row per boat. Matched by SailNo.

Usage:
  python3 apply_boat_updates.py <db.sqlite> <changes.csv>
"""
import sys
import csv
import argparse
import sqlite3
import datetime

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from build_db import get_or_create_sailmaker, get_or_create_owner, norm


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("csv_file")
    return p.parse_args()


def main():
    args = parse_args()
    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    today = datetime.date.today().isoformat()

    with open(args.csv_file, encoding="utf-8") as f:
        lines = [l for l in f if not l.startswith("#")]
    reader = csv.DictReader(lines)

    n_sailmaker = n_owner = n_tcc = 0
    skipped = []

    for row in reader:
        sail_no = norm(row.get("SailNo"))
        if not sail_no:
            continue
        cur.execute("SELECT id, tcc FROM boats WHERE sail_no = ?", (sail_no,))
        boat_row = cur.fetchone()
        if not boat_row:
            skipped.append(sail_no)
            continue
        boat_id, current_tcc = boat_row

        new_sailmaker = norm(row.get("NewSailmaker"))
        if new_sailmaker:
            sm_id = get_or_create_sailmaker(cur, new_sailmaker)
            # close out whatever was previously "current" (last row, effective_to NULL)
            cur.execute(
                "UPDATE boat_sailmaker_history SET effective_to = ? "
                "WHERE boat_id = ? AND effective_to IS NULL", (today, boat_id))
            cur.execute(
                "INSERT INTO boat_sailmaker_history "
                "(boat_id, sailmaker_id, effective_from, source, confidence) "
                "VALUES (?, ?, ?, 'manual:dashboard-edit', 'manual')",
                (boat_id, sm_id, today))
            n_sailmaker += 1

        new_owner = norm(row.get("NewOwner"))
        if new_owner:
            owner_id = get_or_create_owner(cur, new_owner)
            cur.execute("UPDATE boats SET current_owner_id = ?, updated_at = ? WHERE id = ?",
                        (owner_id, datetime.datetime.now().isoformat(), boat_id))
            n_owner += 1

        new_tcc = norm(row.get("NewTCC"))
        if new_tcc:
            try:
                tcc_val = float(new_tcc)
                cur.execute("UPDATE boats SET tcc = ?, updated_at = ? WHERE id = ?",
                            (tcc_val, datetime.datetime.now().isoformat(), boat_id))
                n_tcc += 1
            except ValueError:
                print(f"  Skipping bad TCC value for {sail_no}: {new_tcc!r}")

    conn.commit()
    print(f"Applied: {n_sailmaker} sailmaker change(s), {n_owner} owner change(s), "
          f"{n_tcc} TCC change(s).")
    if skipped:
        print(f"Skipped (sail number not found in DB): {', '.join(skipped)}")
    conn.close()


if __name__ == "__main__":
    main()
