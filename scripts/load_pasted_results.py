#!/usr/bin/env python3
"""
Load results copied out of a browser by Claude in Chrome.

RORC (2023+) and JOG both sit behind robots.txt rules that disallow automated
crawling, so this project cannot scrape them. A person reading a page they are
entitled to read is a different thing entirely, and this is the path for it:
you open the results page, Claude in Chrome transcribes what is on screen, and
the CSV lands here.

Unlike load_rorc_csv.py, which takes the regatta and race on the command line
and so handles exactly one race per run, this CSV is SELF-DESCRIBING - every
row carries its own regatta, year, race and class. One paste can therefore
cover a whole season across many classes, which is the only way this is
tolerable to do by hand.

Columns (header must be present; order does not matter):
  required  Regatta, SeasonYear, RaceName, ClassLabel, SailNo
  optional  RaceDate, Position, Points, BoatName, BoatType, Owner, SailedBy,
            TCC, Elapsed, Corrected, Status, DoubleHanded, Sailmaker,
            SailmakerPartial, SourceUrl

Idempotent: races are keyed on (event, race name, class) and entries on
(race, boat), so re-pasting the same page corrects rather than duplicates.

Usage:
  python3 load_pasted_results.py <db.sqlite> <csv_file> [--category RORC]
                                 [--dry-run] [--allow-non-irc]
"""
import sys
import csv
import argparse
import sqlite3
import re
from collections import defaultdict

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from build_db import (get_or_create_sailmaker, get_or_create_owner, get_or_create_boat,
                      get_or_create_regatta, get_or_create_event, create_race,
                      norm, norm_upper)

REQUIRED = ["Regatta", "SeasonYear", "RaceName", "ClassLabel", "SailNo"]

CREW_SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS race_crew (
    id        INTEGER PRIMARY KEY,
    race_id   INTEGER NOT NULL REFERENCES races(id) ON DELETE CASCADE,
    boat_id   INTEGER NOT NULL REFERENCES boats(id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES people(id),
    role      TEXT,
    UNIQUE(race_id, boat_id, person_id)
);
CREATE INDEX IF NOT EXISTS idx_race_crew_person ON race_crew(person_id);
"""

# Results pages list joint skippers in one cell - "NICK MARTIN, RUARIDH WRIGHT",
# "TIM GOODHEW / KELVIN MATTHEWS", "DEB FISH & ROB CRAIGIE". Kept as one string
# the second name is invisible, and on a double-handed boat that is half the
# crew. Split so each person is findable in their own right.
CREW_SPLIT = re.compile(r"\s*(?:,|&|/|\+|and)\s*", re.I)


def split_crew(raw):
    """Return the individual people named in a Skipper/s cell.

    Deliberately conservative: a fragment must contain a letter and be long
    enough to be a name, so "N/A" or a stray separator does not invent a person.
    """
    raw = norm(raw) or ""
    if not raw:
        return []
    parts = [p.strip(" .") for p in CREW_SPLIT.split(raw)]
    return [p for p in parts if len(p) >= 3 and re.search(r"[A-Za-z]{2}", p)]


def get_or_create_person(cur, name):
    n = norm_upper(name)
    if not n:
        return None
    cur.execute("SELECT id FROM people WHERE name = ?", (n,))
    r = cur.fetchone()
    if r:
        return r[0]
    cur.execute("INSERT INTO people (name) VALUES (?)", (n,))
    return cur.lastrowid

# This tool is IRC-only by decision: one-design fleets are excluded even when
# they sail the same regatta. Anything without IRC in the class label has to
# earn its place some other way, so it is reported and skipped by default.
IRC_CLASS = re.compile(r"\bIRC", re.I)
OD_HINT = re.compile(r"\b(j/?70|j/?80|sb\s?20|xod|x one design|squib|dragon|"
                     r"etchells|daring|sonar|mermaid|redwing|victory|flying 15|"
                     r"contessa 32|sonata|swallow|rs elite|cape 31 one design)\b", re.I)

RETIRED = re.compile(r"\b(dnf|dns|dnc|ret|rtd|withdrew|did not)\b", re.I)
DISQ = re.compile(r"\b(dsq|bfd|ufd|ocs|nsc|scp)\b", re.I)


def num(v, cast=float):
    v = norm(v)
    if not v:
        return None
    try:
        return cast(float(v))
    except (TypeError, ValueError):
        return None


def status_of(row):
    blob = " ".join(x for x in (norm(row.get("Status")), norm(row.get("Comments")),
                                norm(row.get("Corrected")), norm(row.get("Position"))) if x)
    if DISQ.search(blob):
        return "disqualified"
    if RETIRED.search(blob):
        return "retired"
    if norm(row.get("Corrected")) or num(row.get("Position"), int):
        return "finished"
    return "entered"


def truthy(v):
    return (norm(v) or "").strip().lower() in ("y", "yes", "true", "1", "dh", "2h")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("csv_file")
    p.add_argument("--category", default="RORC",
                   help="category for regattas this load creates (RORC / JOG / Club)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--allow-non-irc", action="store_true",
                   help="load classes that do not look like IRC divisions")
    args = p.parse_args()

    with open(args.csv_file, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(l for l in f if not l.lstrip().startswith("#")))
    if not rows:
        sys.exit("no data rows in CSV")
    missing = [c for c in REQUIRED if c not in rows[0]]
    if missing:
        sys.exit(f"CSV is missing required column(s): {', '.join(missing)}")

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    cur.executescript(CREW_SCHEMA)

    n_entries = skipped_od = skipped_blank = n_crew = 0
    races = {}
    per_race = defaultdict(int)
    warnings = []

    for i, row in enumerate(rows, start=2):
        cls = norm(row.get("ClassLabel")) or ""
        sail_no = norm_upper(row.get("SailNo")) or ""
        boat_name = norm(row.get("BoatName")) or ""
        if not sail_no and not boat_name:
            skipped_blank += 1
            continue
        if not args.allow_non_irc and (OD_HINT.search(cls) or not IRC_CLASS.search(cls)):
            skipped_od += 1
            continue

        regatta = norm(row.get("Regatta"))
        year = num(row.get("SeasonYear"), int)
        race_name = norm(row.get("RaceName"))
        if not (regatta and year and race_name and cls):
            warnings.append(f"line {i}: incomplete race identity, skipped")
            continue

        key = (regatta, year, race_name, cls)
        if key not in races:
            regatta_id = get_or_create_regatta(cur, regatta, args.category)
            event_id = get_or_create_event(cur, regatta_id, year,
                                           source_url=norm(row.get("SourceUrl")) or None)
            races[key] = create_race(cur, event_id, race_name, status="confirmed",
                                     class_label=cls,
                                     race_date=norm(row.get("RaceDate")) or None)
        race_id = races[key]

        tcc = num(row.get("TCC"))
        boat_id = get_or_create_boat(cur, sail_no, boat_name, norm(row.get("BoatType")), tcc)
        if boat_id is None:
            skipped_blank += 1
            continue
        owner_id = get_or_create_owner(cur, row.get("Owner"))

        # An asterisk on a sailmaker's own post means a partial inventory - some
        # sails, not the boat's whole wardrobe. Recorded so a partial listing is
        # never counted as a full win.
        sm_raw = norm(row.get("Sailmaker")) or ""
        partial = truthy(row.get("SailmakerPartial")) or sm_raw.endswith("*")
        sm_id = get_or_create_sailmaker(cur, sm_raw.rstrip("*").strip()) if sm_raw else None

        # Double-handed is a layer over the same fleet, not a separate class:
        # the boats also appear in their IRC division. Tagged, so an overlap can
        # be collapsed later instead of double-counting the entry.
        tag = "2H" if truthy(row.get("DoubleHanded")) else None

        cur.execute(
            "INSERT OR REPLACE INTO race_entries "
            "(race_id, boat_id, class, sail_no_used, boat_name_used, boat_type_used, tcc, "
            " owner_id, owner_name_used, skipper_name_used, sailmaker_id, status, "
            " elapsed_time, corrected_time, position, points, comments, tag, source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (race_id, boat_id, cls, sail_no, boat_name, norm(row.get("BoatType")), tcc,
             owner_id, norm(row.get("Owner")), norm(row.get("SailedBy")) or norm(row.get("Owner")),
             sm_id, status_of(row), norm(row.get("Elapsed")), norm(row.get("Corrected")),
             num(row.get("Position"), int), num(row.get("Points")),
             "partial inventory" if partial else norm(row.get("Comments")),
             tag, "paste:claude-in-chrome"))
        # Record every named person, not just the first. A single name is the
        # skipper; where the page names two or more they are listed jointly, so
        # none of them is demoted to a passenger.
        crew = split_crew(row.get("SailedBy"))
        role = "skipper" if len(crew) == 1 else "co-skipper"
        for person in crew:
            pid = get_or_create_person(cur, person)
            if pid:
                cur.execute(
                    "INSERT OR IGNORE INTO race_crew (race_id, boat_id, person_id, role) "
                    "VALUES (?,?,?,?)", (race_id, boat_id, pid, role))
                n_crew += 1

        n_entries += 1
        per_race[key] += 1

    print(f"{n_entries} entr(ies) across {len(races)} race/class combination(s); "
          f"{n_crew} crew placement(s):")
    for (regatta, year, race_name, cls), cnt in sorted(per_race.items()):
        print(f"  {year}  {regatta[:34]:<34} {race_name[:26]:<26} {cls[:14]:<14} {cnt:>3}")
    if skipped_od:
        print(f"\n{skipped_od} row(s) skipped as non-IRC "
              f"(use --allow-non-irc to load them anyway)")
    if skipped_blank:
        print(f"{skipped_blank} row(s) skipped with no sail number or boat name")
    for w in warnings[:10]:
        print(f"  ! {w}")

    if args.dry_run:
        conn.rollback()
        print("\n(dry run - nothing written)")
    else:
        conn.commit()
        print("\ncommitted")
    conn.close()


if __name__ == "__main__":
    main()
