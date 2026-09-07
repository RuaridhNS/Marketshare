#!/usr/bin/env python3
"""
Load Round the Island results read off racing.islandsc.org.uk.

The club's site disallows our crawler in robots.txt, so these rows come out of
the user's own signed-in browser rather than a scraper - see
scripts/README_scraping.md. The CSV that comes back has one row per boat per
class view:

  Year,Class,Pos,BoatName,SailNo,TCC,Skipper,Owner,BoatType,
  Finished,Elapsed,Corrected,Source

Only the IRC 0/1/2/3 rows are loaded as entries. That is not a shortcut: those
four classes partition the fleet exactly (2025: 28+83+71+84 = 266; 2026:
34+73+82+96 = 285, both equal to the site's own overall list), while the other
views on the same dropdown - Double Handed, Clipper Yachts, Line Honours, the
one-design fleets - are *re-cuts of those same boats*. Loading them too would
enter the same boat twice and inflate every share figure computed downstream.

Class labels are written as bare digits ('0'..'3') to match the labels already
in event_class_counts and the canonical vocabulary from normalise_classes.py.

Usage:
  python3 load_rti_islandsc.py <db.sqlite> <csv_file> [--dry-run]
"""
import sys
import csv
import argparse
import sqlite3
import pathlib
import re
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_db import (get_or_create_owner, get_or_create_boat, get_or_create_regatta,
                      get_or_create_event, create_race, norm, norm_upper)

REGATTA = "Round the Island Race"
RACE_NAME = "Round the Island Race"
SOURCE = "browser:islandsc"

# "IRC 2" -> "2". Anything that is not one of the four IRC divisions is a
# re-cut of the same fleet and is deliberately not loaded (see docstring).
IRC_CLASS = re.compile(r"^IRC\s+([0-3])$", re.I)

MONTHS = {m: i for i, m in enumerate(
    "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), start=1)}


def race_date(finished):
    """'07 Jun 2025 12:04:00' -> '2025-06-07'."""
    m = re.match(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})", norm(finished) or "")
    if not m:
        return None
    d, mon, y = m.groups()
    if mon.title() not in MONTHS:
        return None
    return "%s-%02d-%02d" % (y, MONTHS[mon.title()], int(d))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("csv_file")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    by_year_class = defaultdict(list)
    skipped = defaultdict(int)
    with open(args.csv_file, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            m = IRC_CLASS.match(norm(row.get("Class")) or "")
            if not m:
                skipped[norm(row.get("Class"))] += 1
                continue
            by_year_class[(int(row["Year"]), m.group(1))].append(row)

    if skipped:
        print("not loaded (re-cuts of the same boats): "
              + ", ".join("%s x%d" % (k, v) for k, v in sorted(skipped.items())))

    regatta_id = get_or_create_regatta(cur, REGATTA, "Club")
    n_entries = 0
    per_year = defaultdict(int)

    for (year, cls), rows in sorted(by_year_class.items()):
        event_id = get_or_create_event(cur, regatta_id, year)
        rdate = next((race_date(r.get("Finished")) for r in rows
                      if race_date(r.get("Finished"))), None)
        race_id = create_race(cur, event_id, RACE_NAME, status="confirmed",
                              class_label=cls, race_date=rdate)
        for r in rows:
            sail_no = norm_upper(r.get("SailNo"))
            if not sail_no:
                continue
            try:
                tcc = float(r["TCC"]) if norm(r.get("TCC")) else None
            except ValueError:
                tcc = None
            try:
                position = int(r["Pos"]) if norm(r.get("Pos")) else None
            except ValueError:
                position = None
            corrected = norm(r.get("Corrected"))
            boat_id = get_or_create_boat(cur, sail_no, r.get("BoatName"),
                                         norm(r.get("BoatType")), tcc)
            owner_id = get_or_create_owner(cur, r.get("Owner"))
            cur.execute(
                "INSERT OR REPLACE INTO race_entries "
                "(id, race_id, boat_id, class, sail_no_used, boat_name_used, boat_type_used, "
                " tcc, owner_id, owner_name_used, skipper_name_used, status, finish_time, "
                " elapsed_time, corrected_time, position, source) VALUES ("
                " (SELECT id FROM race_entries WHERE race_id = ? AND boat_id = ?),"
                " ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (race_id, boat_id,
                 race_id, boat_id, cls, sail_no, norm_upper(r.get("BoatName")),
                 norm(r.get("BoatType")), tcc, owner_id, norm_upper(r.get("Owner")),
                 norm_upper(r.get("Skipper")), "finished" if corrected else "entered",
                 norm(r.get("Finished")), norm(r.get("Elapsed")), corrected,
                 position, SOURCE))
            n_entries += 1
            per_year[year] += 1

        # The aggregate counts came from the same page, so keep them in step -
        # 2025's were already right, 2026 had no event row at all.
        cur.execute(
            "INSERT OR REPLACE INTO event_class_counts "
            "(id, event_id, class_label, entry_count, source) VALUES ("
            " (SELECT id FROM event_class_counts WHERE event_id = ? AND class_label = ?),"
            " ?, ?, ?, ?)",
            (event_id, cls, event_id, cls, len(rows), SOURCE))
        print("  %d class %s: %d boats" % (year, cls, len(rows)))

    for year, total in sorted(per_year.items()):
        event_id = get_or_create_event(cur, regatta_id, year)
        cur.execute(
            "INSERT OR REPLACE INTO event_class_counts "
            "(id, event_id, class_label, entry_count, source) VALUES ("
            " (SELECT id FROM event_class_counts WHERE event_id = ? AND class_label = 'Total'),"
            " ?, 'Total', ?, ?)",
            (event_id, event_id, total, SOURCE))
        print("%d total: %d" % (year, total))

    print("\n%d entries" % n_entries)
    if args.dry_run:
        conn.rollback()
        print("(dry run - nothing written)")
    else:
        conn.commit()
        print("committed")
    conn.close()


if __name__ == "__main__":
    main()
