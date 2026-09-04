#!/usr/bin/env python3
"""
Replace a boat's ownership history with a corrected one.

Ownership here is inferred: whoever a results page printed in its owner column
becomes the owner for that season. Those pages print whoever ENTERED the boat,
which is often the boat captain, the programme manager, a charter operator or a
co-skipper. 248 timelines in this database show a one-season name sitting
between the same owner either side, and no rule can tell which of those is a
typo, which is a stand-in and which is a real sale.

A person can. This takes what they decided.

The file is a whole timeline per boat, not a patch: the rows given for a boat
ARE its history afterwards. That is deliberate. A patch language ("change row
2, delete row 3") is unreadable a week later and impossible to check; a
timeline you can read top to bottom is neither.

  SailNo,BoatName,Seq,OwnerName,FromYear,ToYear,Charter

ToYear empty means "still the owner". Charter "yes" marks a name that entered
the boat without owning it: it stays on the timeline, because it is true and
useful, but it never becomes the current owner.

Rows are written with confidence 'manual', which outranks every inferred row,
so a re-scrape of the same results page cannot quietly overwrite them.

Usage:
  python3 apply_owner_timeline.py <db.sqlite> <timelines.csv> [--dry-run]
"""
import sys
import csv
import argparse
import sqlite3
import pathlib
import datetime
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_db import get_or_create_owner, norm, norm_upper


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("csv_file")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    has_charter = any(r[1] == "is_charter"
                      for r in cur.execute("PRAGMA table_info(boat_owner_history)"))
    now = datetime.datetime.now().isoformat()

    by_boat = defaultdict(list)
    with open(args.csv_file, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            sail = norm_upper(row.get("SailNo"))
            owner = norm_upper(row.get("OwnerName"))
            if not sail or not owner:
                continue
            by_boat[sail].append({
                "seq": int(norm(row.get("Seq")) or 0),
                "owner": owner,
                "from": norm(row.get("FromYear")) or None,
                "to": norm(row.get("ToYear")) or None,
                "charter": (norm(row.get("Charter")) or "").lower() in ("yes", "y", "true", "1"),
            })

    n_boats = n_rows = 0
    for sail, rows in by_boat.items():
        got = cur.execute("SELECT id, boat_name FROM boats WHERE sail_no = ?", (sail,)).fetchone()
        if not got:
            print(f"  {sail}: not on file, skipped")
            continue
        boat_id, boat_name = got
        rows.sort(key=lambda r: (r["seq"], r["from"] or ""))

        open_rows = [r for r in rows if not r["to"] and not r["charter"]]
        if len(open_rows) > 1:
            print(f"  {sail} {boat_name}: {len(open_rows)} periods have no end year, so the boat "
                  f"would have two current owners. Skipped - give the earlier one a ToYear.")
            continue

        before = cur.execute(
            "SELECT COUNT(*) FROM boat_owner_history WHERE boat_id = ?", (boat_id,)).fetchone()[0]
        print(f"\n{boat_name} ({sail}): {before} row(s) -> {len(rows)}")
        for r in rows:
            print(f"    {r['from'] or '?'}-{r['to'] or 'present'}  {r['owner']}"
                  f"{'  [charter]' if r['charter'] else ''}")

        if not args.dry_run:
            cur.execute("DELETE FROM boat_owner_history WHERE boat_id = ?", (boat_id,))
            for r in rows:
                owner_id = get_or_create_owner(cur, r["owner"])
                cols = "boat_id, owner_id, effective_from, effective_to, source, confidence"
                vals = [boat_id, owner_id, r["from"], r["to"],
                        "manual:timeline-edit", "manual"]
                if has_charter:
                    cols += ", is_charter"
                    vals.append(1 if r["charter"] else 0)
                cur.execute(f"INSERT INTO boat_owner_history ({cols}) "
                            f"VALUES ({','.join('?' * len(vals))})", vals)
            # the current owner is the open period that is not a charter
            current = open_rows[0] if open_rows else None
            if current:
                cur.execute("UPDATE boats SET current_owner_id = ?, updated_at = ? WHERE id = ?",
                            (get_or_create_owner(cur, current["owner"]), now, boat_id))
        n_boats += 1
        n_rows += len(rows)

    print(f"\n{n_boats} boat timeline(s) rewritten, {n_rows} period(s)")
    if args.dry_run:
        conn.rollback()
        print("(dry run - nothing written)")
    else:
        conn.commit()
        print("committed")
    conn.close()


if __name__ == "__main__":
    main()
