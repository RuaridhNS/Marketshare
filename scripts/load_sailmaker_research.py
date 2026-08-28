#!/usr/bin/env python3
"""
Load researched sailmaker findings, keeping the evidence attached.

Race results almost never publish sails - only 151 of 68,553 entries carry any
sailmaker signal - so the rest has to come from published sources: sailmakers'
own customer and ambassador pages, regatta reports, class association write-ups
and brokerage listings.

Every row records WHERE the finding came from and HOW strong it is, because
these are inferences of varying quality and a market-share number built on them
should be auditable back to its source. Nothing here is a guess: if no source
was found the boat stays Unknown, which is more useful than a plausible
invention in a tool used for sales decisions.

Confidence levels:
  high    the sailmaker's own site names this boat as a customer, or a regatta
          report explicitly credits the sails
  medium  a report or listing names the boat and the sailmaker together but
          not unambiguously as supplier, or the evidence is a few years old
  low     circumstantial - same owner's other boat, a dated inventory. Loaded
          but flagged, and worth a human check before acting on it

CSV columns: SailNo,Sailmaker,Confidence,Source,AsOf,Note

Findings are written to boat_sailmaker_history with source "research:<url>",
NOT onto boats.current_sailmaker directly - the dashboard derives current
sailmaker from the newest history row, so this stays reversible by deleting
the rows this script added.

Usage:
  python3 load_sailmaker_research.py <db.sqlite> <findings.csv> [--dry-run]
"""
import sys
import csv
import argparse
import sqlite3
import datetime

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from build_db import get_or_create_sailmaker, norm

VALID_CONFIDENCE = {"high", "medium", "low"}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("csv_file")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    with open(args.csv_file, encoding="utf-8") as f:
        rows = list(csv.DictReader(l for l in f if not l.startswith("#")))

    added = skipped = bad = 0
    for row in rows:
        sail_no = norm(row.get("SailNo"))
        maker = norm(row.get("Sailmaker"))
        conf = (norm(row.get("Confidence")) or "medium").lower()
        src = norm(row.get("Source")) or "research"
        as_of = norm(row.get("AsOf")) or datetime.date.today().isoformat()
        if not sail_no or not maker:
            continue
        if conf not in VALID_CONFIDENCE:
            print(f"  {sail_no}: bad confidence {conf!r}, skipping")
            bad += 1
            continue

        boat = cur.execute("SELECT id, boat_name FROM boats WHERE sail_no = ?", (sail_no,)).fetchone()
        if not boat:
            print(f"  {sail_no}: no such boat in the database, skipping")
            skipped += 1
            continue
        boat_id, boat_name = boat

        # don't stack duplicate findings for the same boat+maker+source
        dup = cur.execute(
            "SELECT 1 FROM boat_sailmaker_history WHERE boat_id = ? AND source = ?",
            (boat_id, f"research:{src}")).fetchone()
        if dup:
            skipped += 1
            continue

        sm_id = get_or_create_sailmaker(cur, maker)
        if not args.dry_run:
            # close whatever was open, then add this as the current finding
            cur.execute("UPDATE boat_sailmaker_history SET effective_to = ? "
                        "WHERE boat_id = ? AND effective_to IS NULL", (as_of, boat_id))
            cur.execute(
                "INSERT INTO boat_sailmaker_history "
                "(boat_id, sailmaker_id, effective_from, source, confidence) "
                "VALUES (?, ?, ?, ?, ?)",
                (boat_id, sm_id, as_of, f"research:{src}", conf))
        print(f"  {sail_no:12} {boat_name[:26]:26} -> {maker:12} [{conf}]  {src[:56]}")
        added += 1

    if args.dry_run:
        conn.rollback()
        print("\n(dry run - nothing written)")
    else:
        conn.commit()
    print(f"\nAdded {added} finding(s); skipped {skipped}; {bad} rejected for bad confidence.")
    conn.close()


if __name__ == "__main__":
    main()
