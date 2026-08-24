#!/usr/bin/env python3
"""
Backfill boat_owner_history and boats.current_owner_id from race_entries.

Every race-results loader (load_rorc_csv.py, scrape_royal_southern.py,
load_manual_template.py) records the owner name on the race_entries row
(owner_id/owner_name_used) but was never propagating it up to the boat
record itself - so most boats had real owner data sitting on their race
history that boats.current_owner_id never picked up.

For each boat, this walks its race entries in season-year order and
detects ownership changes (e.g. a boat sold mid-history shows up under a
different owner from some year onward) as separate boat_owner_history
segments - same "current = last inserted row" convention as
boat_sailmaker_history, so a later manual dashboard edit (which appends
its own row) naturally stays authoritative over a re-run of this backfill.

Idempotent: re-running clears out this script's own previously-inferred
segments for a boat before recomputing, so it never accumulates
duplicates or fights with itself across runs.

Usage:
  python3 backfill_owners.py <db.sqlite>
"""
import sys
import argparse
import sqlite3
from collections import defaultdict

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from build_db import norm_upper

SOURCE = "inferred:race_entries"


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    return p.parse_args()


def main():
    args = parse_args()
    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    rows = cur.execute("""
        SELECT re.boat_id, e.season_year, re.owner_name_used
        FROM race_entries re
        JOIN races r ON r.id = re.race_id
        JOIN events e ON e.id = r.event_id
        WHERE re.owner_name_used IS NOT NULL AND re.owner_name_used != ''
          AND e.season_year IS NOT NULL
    """).fetchall()

    by_boat = defaultdict(list)
    for boat_id, year, owner_name in rows:
        by_boat[boat_id].append((year, norm_upper(owner_name)))

    owner_id_cache = {}
    def owner_id_for(name):
        if name not in owner_id_cache:
            cur.execute("SELECT id FROM owners WHERE name = ?", (name,))
            row = cur.fetchone()
            if row:
                owner_id_cache[name] = row[0]
            else:
                cur.execute("INSERT INTO owners (name) VALUES (?)", (name,))
                owner_id_cache[name] = cur.lastrowid
        return owner_id_cache[name]

    boats_touched = 0
    segments_written = 0
    boats_defaulted_current = 0

    for boat_id, entries in by_boat.items():
        entries.sort(key=lambda x: x[0])

        # collapse consecutive same-owner years into segments
        segments = []
        for year, owner_name in entries:
            if segments and segments[-1]["owner"] == owner_name:
                segments[-1]["to"] = year
            else:
                segments.append({"owner": owner_name, "from": year, "to": year})

        cur.execute("DELETE FROM boat_owner_history WHERE boat_id = ? AND source = ?", (boat_id, SOURCE))

        last_owner_id = None
        for seg in segments:
            oid = owner_id_for(seg["owner"])
            effective_to = None if seg is segments[-1] else str(seg["to"])
            cur.execute(
                "INSERT INTO boat_owner_history (boat_id, owner_id, effective_from, effective_to, source, confidence) "
                "VALUES (?, ?, ?, ?, ?, 'inferred')",
                (boat_id, oid, str(seg["from"]), effective_to, SOURCE))
            last_owner_id = oid
            segments_written += 1

        # only set current_owner_id if nothing more authoritative (a manual
        # edit) has already claimed the "last row" position for this boat
        cur.execute(
            "SELECT source FROM boat_owner_history WHERE boat_id = ? ORDER BY id DESC LIMIT 1", (boat_id,))
        latest_row = cur.fetchone()
        if latest_row and latest_row[0] == SOURCE:
            cur.execute("UPDATE boats SET current_owner_id = ? WHERE id = ?", (last_owner_id, boat_id))
            boats_defaulted_current += 1

        boats_touched += 1

    conn.commit()
    print(f"Processed {boats_touched} boat(s) with owner data on their race history.")
    print(f"Wrote {segments_written} owner-history segment(s); "
          f"set current_owner_id on {boats_defaulted_current} boat(s).")
    conn.close()


if __name__ == "__main__":
    main()
