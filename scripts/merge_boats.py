#!/usr/bin/env python3
"""
Merge duplicate/renamed boat records identified in the dashboard (e.g. a
sail-number suffix typo like GBR9091R vs GBR9091, or a boat that changed
name and got re-registered as a "new" boat) into a single canonical boat,
without losing any history.

- race_entries and boat_sailmaker_history move from the merged-away boat
  to the keeper. If both boats somehow have an entry for the exact same
  race (a true duplicate row), the keeper's entry wins and the other is
  dropped.
- Scalar fields (owner, boat type, TCC) are backfilled onto the keeper
  from the merged-away boat only where the keeper's value is missing.
- Per-entry historical fields (sail_no_used, boat_name_used, owner_name_used
  etc.) are untouched, so a boat's full name/owner history across a rename
  stays visible on the merged race_entries rows even after the merge.

Usage:
  python3 merge_boats.py <db.sqlite> <merges.csv>

CSV columns: KeepSailNo,MergeSailNo,Notes
"""
import sys
import csv
import argparse
import sqlite3

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from build_db import norm


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("csv_file")
    return p.parse_args()


def merge_one(cur, keep_id, merge_id):
    # Record the merged-away sail number as an alias of the keeper FIRST, so a
    # later scrape that meets that number again resolves to the keeper instead
    # of re-creating the boat. Without this every merge was undone by the next
    # scrape - 20 of them had silently come back.
    cur.execute("""CREATE TABLE IF NOT EXISTS boat_sail_aliases (
                     alias_sail_no TEXT PRIMARY KEY,
                     boat_id       INTEGER NOT NULL REFERENCES boats(id) ON DELETE CASCADE,
                     created_at    TEXT NOT NULL DEFAULT (datetime('now')))""")
    cur.execute("SELECT sail_no FROM boats WHERE id = ?", (merge_id,))
    row = cur.fetchone()
    if row and row[0]:
        cur.execute("INSERT OR REPLACE INTO boat_sail_aliases (alias_sail_no, boat_id) VALUES (?, ?)",
                    (row[0], keep_id))
    # any aliases already pointing at the merged-away boat must follow it
    cur.execute("UPDATE boat_sail_aliases SET boat_id = ? WHERE boat_id = ?", (keep_id, merge_id))

    # race_entries: reassign, but drop the merged-away row if the keeper
    # already has an entry for that race (true duplicate row).
    cur.execute("SELECT race_id FROM race_entries WHERE boat_id = ?", (keep_id,))
    keeper_races = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT id, race_id FROM race_entries WHERE boat_id = ?", (merge_id,))
    for entry_id, race_id in cur.fetchall():
        if race_id in keeper_races:
            cur.execute("DELETE FROM race_entries WHERE id = ?", (entry_id,))
        else:
            cur.execute("UPDATE race_entries SET boat_id = ? WHERE id = ?", (keep_id, entry_id))

    cur.execute("UPDATE boat_sailmaker_history SET boat_id = ? WHERE boat_id = ?", (keep_id, merge_id))
    # Ownership timeline must survive the merge too. Without this the
    # merged-away boat's owner history died with it (ON DELETE CASCADE), losing
    # exactly the previous-owner record a duplicate pair usually exists to
    # document - the two records often ARE the same hull under two owners.
    cur.execute("UPDATE boat_owner_history SET boat_id = ? WHERE boat_id = ?", (keep_id, merge_id))

    # backfill scalar fields onto the keeper where missing
    cur.execute("SELECT boat_name, boat_type, tcc, current_owner_id FROM boats WHERE id = ?", (keep_id,))
    k_name, k_type, k_tcc, k_owner = cur.fetchone()
    cur.execute("SELECT boat_type, tcc, current_owner_id FROM boats WHERE id = ?", (merge_id,))
    m_type, m_tcc, m_owner = cur.fetchone()
    cur.execute(
        "UPDATE boats SET boat_type = COALESCE(?, boat_type), tcc = COALESCE(?, tcc), "
        "current_owner_id = COALESCE(?, current_owner_id) WHERE id = ?",
        (k_type or m_type, k_tcc if k_tcc is not None else m_tcc, k_owner or m_owner, keep_id))

    # boat_crm: backfill any field the keeper is missing
    cur.execute("SELECT lead_rep, contacted_by, in_cs, tag, notes, boat_captain, programme_manager FROM boat_crm WHERE boat_id = ?", (keep_id,))
    keep_crm = cur.fetchone()
    cur.execute("SELECT lead_rep, contacted_by, in_cs, tag, notes, boat_captain, programme_manager FROM boat_crm WHERE boat_id = ?", (merge_id,))
    merge_crm = cur.fetchone()
    if merge_crm:
        if keep_crm:
            # 7 CRM fields now (boat_captain and programme_manager added);
            # keep whatever the keeper already has, fall back to the other record
            merged = [keep_crm[i] if keep_crm[i] not in (None, "") else merge_crm[i]
                      for i in range(7)]
            cur.execute(
                "UPDATE boat_crm SET lead_rep=?, contacted_by=?, in_cs=?, tag=?, notes=?, "
                "boat_captain=?, programme_manager=? WHERE boat_id=?",
                (*merged, keep_id))
        else:
            cur.execute(
                "INSERT INTO boat_crm (boat_id, lead_rep, contacted_by, in_cs, tag, notes, "
                "boat_captain, programme_manager) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (keep_id, *merge_crm))

    cur.execute("DELETE FROM boats WHERE id = ?", (merge_id,))


def main():
    args = parse_args()
    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    with open(args.csv_file, encoding="utf-8") as f:
        lines = [l for l in f if not l.startswith("#")]
    reader = csv.DictReader(lines)

    merged = 0
    skipped = []
    for row in reader:
        keep_sail_no = norm(row.get("KeepSailNo"))
        merge_sail_no = norm(row.get("MergeSailNo"))
        if not keep_sail_no or not merge_sail_no:
            continue
        cur.execute("SELECT id FROM boats WHERE sail_no = ?", (keep_sail_no,))
        keep_row = cur.fetchone()
        cur.execute("SELECT id FROM boats WHERE sail_no = ?", (merge_sail_no,))
        merge_row = cur.fetchone()
        if not keep_row or not merge_row:
            skipped.append(f"{keep_sail_no} <- {merge_sail_no} (boat not found)")
            continue
        if keep_row[0] == merge_row[0]:
            skipped.append(f"{keep_sail_no} <- {merge_sail_no} (same boat)")
            continue
        merge_one(cur, keep_row[0], merge_row[0])
        merged += 1

    conn.commit()
    print(f"Merged {merged} boat pair(s).")
    if skipped:
        print("Skipped: " + "; ".join(skipped))
    conn.close()


if __name__ == "__main__":
    main()
