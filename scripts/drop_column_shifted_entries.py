#!/usr/bin/env python3
"""Drop entries a spreadsheet import read one column out of step.

The Vineyard Race 2025 workbook was parsed twice, the second time shifted by a
column, so 79 rows arrived with the spreadsheet's ROW NUMBER as the sail
number, the real sail number as the boat name, and the owner's home town as
the boat type:

    sail_no_used '2'   boat_name_used 'USA 43968'  boat_type_used 'Darien CT United States of America'

Every one of them has a correctly-parsed twin in the same race - USA 43968 is
Alibi, a J/120 - and UNIQUE(race_id, boat_id) did not catch the pair because
the row numbers sent them to different boat records. So 79 real boats picked
up an entry that belongs to someone else, and 79 more carried a town as their
type.

The test is deliberately narrow: an address where a boat type should be, AND a
bare number where a sail number should be. Both at once is a parse that has
slipped a column; either alone is just untidy data.

    python scripts/drop_column_shifted_entries.py db/marketshare.db
    python scripts/drop_column_shifted_entries.py db/marketshare.db --apply
"""
import argparse, datetime, pathlib, re, shutil, sqlite3

ADDRESS = re.compile(r"(United States of America|Canada|United Kingdom)\s*$", re.I)
ROW_NUMBER = re.compile(r"^\d{1,3}$")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    dbp = pathlib.Path(a.db)
    con = sqlite3.connect(dbp)
    cur = con.cursor()
    rows = [r for r in cur.execute(
        "SELECT re.id, re.race_id, re.boat_id, re.sail_no_used, re.boat_name_used, "
        "re.boat_type_used FROM race_entries re WHERE re.boat_type_used IS NOT NULL")
        if ADDRESS.search(r[5] or "") and ROW_NUMBER.match((r[3] or "").strip())]

    if not rows:
        print("no column-shifted entries on file")
        return

    races = {}
    for r in rows:
        races.setdefault(r[1], []).append(r)
    print(f"{len(rows)} column-shifted entr(y/ies) across {len(races)} race(s)")
    for rid, rs in races.items():
        name = cur.execute(
            "SELECT rg.name, ev.season_year FROM races ra JOIN events ev ON ev.id = ra.event_id "
            "JOIN regattas rg ON rg.id = ev.regatta_id WHERE ra.id = ?", (rid,)).fetchone()
        twins = sum(1 for r in rs if cur.execute(
            "SELECT 1 FROM race_entries WHERE race_id = ? AND sail_no_used = ? AND id <> ?",
            (rid, (r[4] or "").strip(), r[0])).fetchone())
        print(f"   race {rid} {name[0][:36] if name else '?'} {name[1] if name else ''}: "
              f"{len(rs)} row(s), {twins} with the correctly-parsed twin in the same race")
        for r in rs[:3]:
            print(f"      sail={r[3]!r} name={r[4]!r} type={r[5][:34]!r}")

    if not a.apply:
        print("\nDRY RUN - pass --apply")
        return

    bak = dbp.with_suffix(f".db.bak-shift-{datetime.datetime.now():%H%M%S}")
    shutil.copy2(dbp, bak)
    print("backup ->", bak.name)
    cur.executemany("DELETE FROM race_entries WHERE id = ?", [(r[0],) for r in rows])
    con.commit()
    print(f"\ndeleted {len(rows)} entr(y/ies)")


if __name__ == "__main__":
    main()
