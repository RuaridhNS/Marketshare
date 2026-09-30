#!/usr/bin/env python3
"""
Put a FIXTURE in the calendar - a regatta that has not been sailed yet.

Every other loader here starts from something that happened: a result, an
entry list, a published entry count. A fixture is earlier than all of them.
Antigua Sailing Week 2027 has dates and nothing else, and for sales that is
the most useful moment of all - it is the one point where there is still time
to talk to a boat before it buys sails for the regatta.

WHY A ROW ALONE IS NOT ENOUGH. export_dashboard_data.py drops any event
holding neither a race nor a reported entry count, because in the regatta tree
such an event is a row that opens onto nothing. A fixture has neither. So this
writes a placeholder race carrying status 'scheduled' - the same status
load_entry_list.py gives a race whose results have not arrived - which is what
keeps the fixture in the export and on the calendar. It holds no entries, so
it adds nothing to any boat count or share figure; it only says "this is
happening, on these dates".

When the entry list arrives, load_entry_list.py fills that same event: races
are keyed on (event, race name, class) and entries on (race, boat), so the
fixture becomes the real event rather than a duplicate beside it.

Idempotent, like the other ledgers: a fixture is keyed on (regatta name,
season). A second run updates the dates if the ledger changed and otherwise
reports no change, which is what makes it safe in refresh_all.py.

Usage:
  python3 add_fixtures.py <db.sqlite> [--file data/fixtures.csv] [--dry-run]
"""
import csv
import argparse
import sqlite3
import pathlib


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--file", default="data/fixtures.csv")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    path = pathlib.Path(args.file)
    if not path.exists():
        print(f"no ledger at {path} - nothing to add")
        return

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    created_rg = created_ev = created_race = updated = unchanged = 0

    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = (row.get("Regatta") or "").strip()
            season = (row.get("Season") or "").strip()
            if not name or not season:
                continue
            season = int(season)
            start = (row.get("Start") or "").strip() or None
            end = (row.get("End") or "").strip() or None
            venue = (row.get("Venue") or "").strip() or None
            country = (row.get("Country") or "").strip() or None
            segment = (row.get("Segment") or "").strip() or None
            source = (row.get("Source") or "").strip() or None

            rg = cur.execute("SELECT id FROM regattas WHERE name = ?", (name,)).fetchone()
            if rg:
                rg_id = rg[0]
            else:
                if not args.dry_run:
                    cur.execute(
                        "INSERT INTO regattas (name, category, region, segment, country, venue) "
                        "VALUES (?,?,?,?,?,?)",
                        (name, "Fixture", "Non-UK", segment, country, venue))
                    rg_id = cur.lastrowid
                else:
                    rg_id = None
                created_rg += 1
                print(f"  regatta created: {name!r} ({venue or country or 'no venue'}, {segment})")

            if rg_id is None:           # dry run, nothing further to key on
                continue

            ev = cur.execute("SELECT id, start_date, end_date FROM events "
                             "WHERE regatta_id = ? AND season_year = ?",
                             (rg_id, season)).fetchone()
            if ev:
                ev_id = ev[0]
                if (ev[1] or None, ev[2] or None) != (start, end):
                    if not args.dry_run:
                        cur.execute("UPDATE events SET start_date = ?, end_date = ? WHERE id = ?",
                                    (start, end, ev_id))
                    print(f"  dates updated: {name!r} {season}  {start}..{end or ''}")
                    updated += 1
                else:
                    unchanged += 1
            else:
                if not args.dry_run:
                    cur.execute("INSERT INTO events (regatta_id, season_year, start_date, "
                                "end_date, source_url, notes) VALUES (?,?,?,?,?,?)",
                                (rg_id, season, start, end, source, "fixture"))
                    ev_id = cur.lastrowid
                else:
                    ev_id = None
                created_ev += 1
                print(f"  fixture added: {name!r} {season}  {start}..{end or ''}")

            if ev_id is None:
                continue

            # the placeholder that keeps the fixture in the export
            race = cur.execute("SELECT id FROM races WHERE event_id = ? AND race_name = ?",
                               (ev_id, name)).fetchone()
            if not race:
                if not args.dry_run:
                    cur.execute("INSERT INTO races (event_id, race_name, race_date, status, "
                                "source_url) VALUES (?,?,?,?,?)",
                                (ev_id, name, start, "scheduled", source))
                created_race += 1

    print(f"\n{created_rg} regatta(s) created, {created_ev} fixture(s) added, "
          f"{created_race} placeholder race(s), {updated} date change(s), "
          f"{unchanged} unchanged")
    if args.dry_run:
        conn.rollback()
        print("(dry run - nothing written)")
    else:
        conn.commit()
        print("committed")
    conn.close()


if __name__ == "__main__":
    main()
