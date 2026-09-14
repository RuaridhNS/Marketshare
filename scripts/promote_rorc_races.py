#!/usr/bin/env python3
"""
Lift RORC's races out of the season-bucket regattas they were scraped into.

WHY THE DASHBOARD LOOKED EMPTY. The RORC legacy archive publishes a whole
season on one page, so the scraper filed every race of 2011-2022 under a single
regatta called "RORC Mainseries" (plus one per class, and a "RORC 2007 IRC 1"
shape for the oldest years). 1,048 races sit in those buckets, and their real
identity is only in races.race_name - "Cervantes Trophy Race", "Morgan Cup
Race", "Channel Race".

Meanwhile the 2023+ results, which come per race, were loaded as regattas in
their own right. So the dashboard shows "RORC Cervantes Trophy Race" with three
seasons in it and nothing before 2023, when the database actually holds that
race back to 2011. Every RORC race in the tree is short in exactly this way.

WHAT THIS DOES. For each race inside a bucket, the ledger says which regatta it
really belongs to; the race is moved to that regatta's event for its own
season, creating the regatta or the event if neither exists yet. Nothing is
deleted and no entry is touched - only races.event_id changes - so the boats,
results and curated fields travel with the race.

It is idempotent because a promoted race is no longer in a bucket: run it twice
and the second run moves nothing. Buckets left holding no races are removed, as
are the events under them, because an empty regatta is a row in the tree that
opens onto nothing.

A race name that is not in the ledger is LEFT WHERE IT IS and reported. That is
deliberate: a wrong guess here silently rewrites which race a result belongs
to, which is far worse than a bucket that is still too full.

Run this BEFORE merge_regattas.py, so the sponsor-variant folds in
data/regatta_merges.csv can act on the regattas this creates.

Usage:
  python3 promote_rorc_races.py <db.sqlite> [--file data/rorc_race_regattas.csv]
      [--dry-run]
"""
import csv
import re
import argparse
import sqlite3
import pathlib
from collections import defaultdict

# The scraped-season buckets. Matched on name because that is what makes them
# buckets - they are named for a season or a class, not for a race.
BUCKET_SQL = "(name LIKE 'RORC Mainseries%' OR name GLOB 'RORC 20[0-9][0-9]*')"


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--file", default="data/rorc_race_regattas.csv")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    path = pathlib.Path(args.file)
    if not path.exists():
        raise SystemExit(f"no ledger at {path}")
    ledger = {}
    for row in csv.DictReader(path.open(encoding="utf-8-sig")):
        rn = norm(row.get("RaceName"))
        rg = norm(row.get("Regatta"))
        if rn and rg:
            ledger[rn.lower()] = rg

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    buckets = {rid: nm for rid, nm in
               cur.execute(f"SELECT id, name FROM regattas WHERE {BUCKET_SQL}")}
    if not buckets:
        print("no bucket regattas left - nothing to promote")
        return
    print(f"{len(buckets)} bucket regatta(s): "
          + ", ".join(sorted(buckets.values())[:4])
          + (" ..." if len(buckets) > 4 else ""))

    regattas = {norm(nm).lower(): rid for rid, nm in cur.execute("SELECT id, name FROM regattas")}
    events = {}
    for eid, rgid, yr in cur.execute("SELECT id, regatta_id, season_year FROM events"):
        events[(rgid, yr)] = eid

    def regatta_id(name):
        key = norm(name).lower()
        if key in regattas:
            return regattas[key]
        cur.execute("INSERT INTO regattas (name, category, region) VALUES (?,?,?)",
                    (name, "RORC", "Solent"))
        regattas[key] = cur.lastrowid
        created_regattas.append(name)
        return regattas[key]

    def event_id(rgid, yr):
        if (rgid, yr) in events:
            return events[(rgid, yr)]
        cur.execute("INSERT INTO events (regatta_id, season_year) VALUES (?,?)", (rgid, yr))
        events[(rgid, yr)] = cur.lastrowid
        return events[(rgid, yr)]

    rows = cur.execute(f"""
        SELECT ra.id, ra.race_name, e.season_year, e.regatta_id
        FROM races ra JOIN events e ON e.id = ra.event_id
        WHERE e.regatta_id IN (SELECT id FROM regattas WHERE {BUCKET_SQL})""").fetchall()

    created_regattas = []
    moved = defaultdict(int)
    unmatched = defaultdict(int)
    for race_id, race_name, yr, _ in rows:
        target = ledger.get(norm(race_name).lower())
        if not target:
            unmatched[norm(race_name)] += 1
            continue
        rgid = regatta_id(target)
        eid = event_id(rgid, yr)
        moved[target] += 1
        if not args.dry_run:
            cur.execute("UPDATE races SET event_id = ? WHERE id = ?", (eid, race_id))

    total = sum(moved.values())
    print(f"\n{total} race(s) promoted out of the buckets, into {len(moved)} regatta(s):")
    for name in sorted(moved, key=lambda k: -moved[k]):
        print(f"    {moved[name]:5}  {name}")
    if created_regattas:
        print(f"\n{len(created_regattas)} regatta(s) created: "
              + ", ".join(sorted(set(created_regattas))))

    if unmatched:
        print(f"\n{sum(unmatched.values())} race(s) left in place - no ledger row for these "
              f"names. Add them to {path} rather than letting them stay buried:")
        for nm in sorted(unmatched, key=lambda k: -unmatched[k]):
            print(f"    {unmatched[nm]:5}  {nm}")

    # An emptied bucket is a dead row in the regatta tree.
    if not args.dry_run:
        cur.execute(f"""DELETE FROM events WHERE regatta_id IN
                        (SELECT id FROM regattas WHERE {BUCKET_SQL})
                        AND id NOT IN (SELECT DISTINCT event_id FROM races)""")
        gone_events = cur.rowcount
        cur.execute(f"""DELETE FROM regattas WHERE {BUCKET_SQL}
                        AND id NOT IN (SELECT DISTINCT regatta_id FROM events)""")
        print(f"\ncleaned up {gone_events} empty event(s) and {cur.rowcount} empty bucket regatta(s)")

    left = cur.execute(f"""SELECT COUNT(*) FROM races ra JOIN events e ON e.id = ra.event_id
                           WHERE e.regatta_id IN (SELECT id FROM regattas WHERE {BUCKET_SQL})
                        """).fetchone()[0]
    print(f"{left} race(s) still in a bucket")

    if args.dry_run:
        conn.rollback()
        print("(dry run - nothing written)")
    else:
        conn.commit()
        print("committed")
    conn.close()


if __name__ == "__main__":
    main()
