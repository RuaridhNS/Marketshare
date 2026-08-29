#!/usr/bin/env python3
"""
Apply boat-level edits (sailmaker / owner / IRC TCC / lead rep / contacted by)
made in the dashboard back into the database. The dashboard can't write to
the DB itself (it's a static file) - instead it lets you make edits in the
browser, then export them as a CSV in this shape:

    SailNo,BoatName,NewSailmaker,NewOwner,NewTCC,NewLeadRep,NewContactedBy,
    NewBoatCaptain,NewProgrammeManager,Notes

Any of the New* columns may be blank (= no change to that field). One row
per boat. Matched by SailNo.

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

    n_sailmaker = n_owner = n_tcc = n_crm = 0
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

        # crew roles: a boat captain or programme manager is often the name a
        # results system records as "owner", so capturing them separately keeps
        # the owner field meaning the actual owner
        for col, field in (("NewBoatCaptain", "boat_captain"),
                           ("NewProgrammeManager", "programme_manager")):
            val = norm(row.get(col))
            if val:
                cur.execute(f"INSERT INTO boat_crm (boat_id, {field}) VALUES (?,?) "
                            f"ON CONFLICT(boat_id) DO UPDATE SET {field}=excluded.{field}, "
                            f"last_updated=?", (boat_id, val, datetime.datetime.now().isoformat()))
                n_crm += 1

        new_lead_rep = norm(row.get("NewLeadRep"))
        new_contacted_by = norm(row.get("NewContactedBy"))
        if new_lead_rep or new_contacted_by:
            cur.execute("SELECT boat_id FROM boat_crm WHERE boat_id = ?", (boat_id,))
            if cur.fetchone():
                if new_lead_rep:
                    cur.execute("UPDATE boat_crm SET lead_rep = ?, last_updated = ? WHERE boat_id = ?",
                                (new_lead_rep, datetime.datetime.now().isoformat(), boat_id))
                if new_contacted_by:
                    cur.execute("UPDATE boat_crm SET contacted_by = ?, last_updated = ? WHERE boat_id = ?",
                                (new_contacted_by, datetime.datetime.now().isoformat(), boat_id))
            else:
                cur.execute(
                    "INSERT INTO boat_crm (boat_id, lead_rep, contacted_by) VALUES (?, ?, ?)",
                    (boat_id, new_lead_rep, new_contacted_by))
            n_crm += 1

    conn.commit()
    print(f"Applied: {n_sailmaker} sailmaker change(s), {n_owner} owner change(s), "
          f"{n_tcc} TCC change(s), {n_crm} lead-rep/contacted-by change(s).")
    if skipped:
        print(f"Skipped (sail number not found in DB): {', '.join(skipped)}")
    conn.close()


if __name__ == "__main__":
    main()
