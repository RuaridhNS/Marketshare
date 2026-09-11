#!/usr/bin/env python3
"""
Give events the dates their sources never published, from a hand-kept ledger.

WHY THIS EXISTS. normalise_dates.py derives an event's span from its races, so
an event is dated only when its results happen to carry dates. That covers 84
of 405 events. The other 321 are not undated because nobody knows when they
happened - every one of these regattas publishes a fixture list months in
advance - they are undated because the RESULTS pages do not repeat the date.
Cowes Week 2026 ran 1-7 August; the results pages simply say "Day 1".

So the date comes from the fixture list instead, recorded here once, by hand,
with the URL it came from. That is a correction ledger like data/boat_merges.csv,
and it is re-applied on every refresh for the same reason: a rebuild must not
quietly lose a fact a human established.

IT ALSO BUILDS FUTURE SEASONS. A row whose (regatta, season) has no event yet
creates one. That is how next year's calendar exists before a single result is
published: the fixture is a fact the moment the club announces it, and an event
with a date and no races renders on the timeline as a date we hold nothing for -
which is precisely the thing a rep wants to see in October.

A ledger date always wins over a derived one, so run this AFTER normalise_dates.

Ledger columns (data/event_dates.csv):
    regatta      exact regattas.name - refused rather than fuzzy-matched
    season       four digits
    start_date   YYYY-MM-DD
    end_date     YYYY-MM-DD, blank for a one-day race
    source       the URL the date was published on. Not optional.
    note         free text

Usage:
  python3 apply_event_dates.py <db.sqlite> [--file data/event_dates.csv] [--dry-run]
"""
import csv
import re
import argparse
import sqlite3
import pathlib

ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--file", default="data/event_dates.csv")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    path = pathlib.Path(args.file)
    if not path.exists():
        print(f"no ledger at {path} - nothing to apply")
        return

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    regattas = {name: rid for rid, name in cur.execute("SELECT id, name FROM regattas")}

    set_ = created = same = 0
    unknown, bad = [], []
    for ln, row in enumerate(csv.DictReader(path.open(encoding="utf-8-sig")), start=2):
        name = (row.get("regatta") or "").strip()
        season = (row.get("season") or "").strip()
        start = (row.get("start_date") or "").strip()
        end = (row.get("end_date") or "").strip() or start
        if not name or name.startswith("#"):
            continue
        if not (season.isdigit() and ISO.fullmatch(start) and ISO.fullmatch(end)):
            bad.append((ln, name, season, start, end))
            continue
        if end < start:
            bad.append((ln, name, season, start, end))
            continue
        rid = regattas.get(name)
        if rid is None:
            unknown.append((ln, name))
            continue
        ev = cur.execute("SELECT id, start_date, end_date FROM events "
                         "WHERE regatta_id = ? AND season_year = ?",
                         (rid, int(season))).fetchone()
        if ev is None:
            created += 1
            if not args.dry_run:
                cur.execute("INSERT INTO events (regatta_id, season_year, start_date, end_date) "
                            "VALUES (?,?,?,?)", (rid, int(season), start, end))
            continue
        if (ev[1], ev[2]) == (start, end):
            same += 1
            continue
        set_ += 1
        if not args.dry_run:
            cur.execute("UPDATE events SET start_date = ?, end_date = ? WHERE id = ?",
                        (start, end, ev[0]))

    print(f"event dates: {set_} set from the ledger, {created} future event(s) created, "
          f"{same} already correct")
    if unknown:
        # A typo here is silent damage - it would look like the date just did
        # not apply - so it is named rather than skipped quietly.
        print(f"\n{len(unknown)} row(s) name a regatta that does not exist. Check the "
              f"spelling against regattas.name:")
        for ln, n in unknown:
            print(f"    line {ln}: {n!r}")
    if bad:
        print(f"\n{len(bad)} row(s) had an unusable date and were skipped:")
        for ln, n, s, a, b in bad:
            print(f"    line {ln}: {n!r} {s} {a!r}..{b!r}")

    dated = cur.execute("SELECT COUNT(*) FROM events WHERE start_date IS NOT NULL").fetchone()[0]
    total = cur.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    print(f"\n{dated} of {total} events now carry a date")

    if args.dry_run:
        conn.rollback()
        print("(dry run - nothing written)")
    else:
        conn.commit()
        print("committed")
    conn.close()


if __name__ == "__main__":
    main()
