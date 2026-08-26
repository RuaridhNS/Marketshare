#!/usr/bin/env python3
"""
Load a RORC race-results CSV (as extracted by WebFetch from a
rorc.org/raceresults/<year>/<slug>.html page - see scripts/README_scraping.md
for the extraction prompt) into the Marketshare database.

CSV columns expected: Position,Points,SailNo,Boat,BoatType,Owner,SailedBy,
FinishTime,Elapsed,Handicap,Corrected,Comments
The first line may be a '# ...' comment with race metadata (ignored here -
race metadata is passed on the command line instead, so it's always
explicit and auditable).

Usage:
  python3 load_rorc_csv.py <db.sqlite> <csv_file> \
      --regatta "RORC Inshore Series" --year 2021 \
      --race-name "Castle Rock Race" --class "IRC Overall" \
      --source-url "https://www.rorc.org/raceresults/2021/ircoverall11.html"
"""
import sys
import csv
import argparse
import sqlite3
import re

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from build_db import (get_or_create_sailmaker, get_or_create_owner, get_or_create_boat,
                       get_or_create_regatta, get_or_create_event, create_race, norm, norm_upper)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("csv_file")
    p.add_argument("--regatta", required=True)
    p.add_argument("--year", required=True, type=int)
    p.add_argument("--race-name", required=True)
    p.add_argument("--class", dest="class_label", default=None)
    p.add_argument("--source-url", default=None)
    # Provenance tag written onto every race_entries row. Defaults to the
    # RORC value this loader was originally written for; every other
    # scraper (Cowes, Royal Southern, Warsash, Hamble) passes its own.
    p.add_argument("--source", default="scrape:rorc")
    p.add_argument("--category", default="RORC")
    return p.parse_args()


def status_from_comments(comments, corrected):
    c = (comments or "").upper()
    if "DNF" in c:
        return "dnf"
    if "DNS" in c:
        return "dns"
    if "DSQ" in c or "DISQUALIF" in c:
        return "dsq"
    if "RET" in c:
        return "retired"
    if "OCS" in c:
        return "ocs"
    return "finished" if corrected else "entered"


def main():
    args = parse_args()
    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    with open(args.csv_file) as f:
        lines = [l for l in f if not l.startswith("#")]
    reader = csv.DictReader(lines)

    regatta_id = get_or_create_regatta(cur, args.regatta, args.category)
    event_id = get_or_create_event(cur, regatta_id, args.year, source_url=args.source_url)
    race_id = create_race(cur, event_id, args.race_name, status="confirmed",
                          class_label=args.class_label)

    n = 0
    for row in reader:
        sail_no = row.get("SailNo")
        if not norm(sail_no):
            continue
        boat_name = row.get("Boat")
        boat_type = row.get("BoatType")
        owner_name = row.get("Owner")
        skipper = row.get("SailedBy") or owner_name
        try:
            tcc = float(row.get("Handicap")) if norm(row.get("Handicap")) else None
        except ValueError:
            tcc = None
        try:
            position = int(float(row.get("Position"))) if norm(row.get("Position")) else None
        except ValueError:
            position = None
        try:
            points = float(row.get("Points")) if norm(row.get("Points")) else None
        except ValueError:
            points = None
        comments = row.get("Comments")
        corrected = norm(row.get("Corrected"))
        status = status_from_comments(comments, corrected)

        boat_id = get_or_create_boat(cur, sail_no, boat_name, boat_type, tcc)
        owner_id = get_or_create_owner(cur, owner_name)

        cur.execute(
            "INSERT OR REPLACE INTO race_entries "
            "(id, race_id, boat_id, class, sail_no_used, boat_name_used, boat_type_used, tcc, "
            " owner_id, owner_name_used, skipper_name_used, status, finish_time, elapsed_time, "
            " corrected_time, position, points, comments, source) VALUES ("
            " (SELECT id FROM race_entries WHERE race_id = ? AND boat_id = ?),"
            " ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (race_id, boat_id,
             race_id, boat_id, args.class_label, norm_upper(sail_no), norm_upper(boat_name),
             norm(boat_type), tcc, owner_id, norm_upper(owner_name), norm_upper(skipper),
             status, norm(row.get("FinishTime")), norm(row.get("Elapsed")), corrected,
             position, points, norm(comments), args.source),
        )
        n += 1

    conn.commit()
    print(f"Loaded {n} entries into race '{args.race_name}' ({args.regatta} {args.year}).")
    conn.close()


if __name__ == "__main__":
    main()
