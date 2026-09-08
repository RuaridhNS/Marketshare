#!/usr/bin/env python3
"""
Write the Round the Island rows back out to the CSV load_rti_islandsc.py reads.

Those 551 entries came out of the user's own signed-in browser, because
racing.islandsc.org.uk disallows this project's crawler. The CSV was never
saved. Every other source here can be rebuilt - a scraper, a tracked
spreadsheet, a tracked decisions file - and this one could not: rebuilding the
database from scratch would have lost the biggest fleet in the Solent, and
getting it back would have meant redoing the whole browser session by hand.

So this reconstructs the file from what was loaded, and the result is a real
input: feeding it back through load_rti_islandsc.py reproduces the same entries.

RELOADING IT NEEDS THE LEDGERS AFTERWARDS, and this was checked rather than
assumed. Loading the file into a copy of the database produced 552 entries where
the original had 551. The extra one is VENOMOUS: Round the Island published it
under GBR7017R, which is Tortuga Marine's Botin 56 BLACK PEARL, and the CSV
faithfully preserves that because it is what the source said. So a reload
re-creates the collision that data/boat_merges.csv exists to fix. Running the
merge ledger afterwards removes it again ("0 moved, 1 already present") and
re-identifies GBR7017R as BLACK PEARL, and the copy then matches the original
exactly - same 551 entries, same position sum, same rating sum. That is the
order refresh_all.py already runs in, so a reload through the pipeline is
faithful; a reload on its own is not.

This is NOT in refresh_all.py, deliberately. It writes a source file FROM the
database, so running it on a schedule would mean a damaged database quietly
overwriting the only copy of its own source. Run it by hand when the loaded
data changes.

WHAT IT CANNOT RECOVER, stated plainly rather than left to be discovered. The
loader only ever stored the IRC 0-3 rows; the other views on the site's dropdown
(Double Handed, Clipper Yachts, Line Honours, the one-design fleets) were read
and deliberately discarded as re-cuts of those same boats, so they are not in
the database and cannot come back out of it. That is the right call for entries
- loading them would have counted boats twice - but it means this file is the
loadable subset, not a copy of what the browser saw. Names and types also come
back upper-cased, because that is how they are stored; the loader upper-cases
them anyway, so the round trip is stable even though it is not byte-identical.

Usage:
  python3 export_rti_source.py <db.sqlite> [--out data/rti_islandsc.csv]
"""
import csv
import argparse
import sqlite3
import pathlib

REGATTA = "Round the Island Race"
SOURCE = "browser:islandsc"
COLUMNS = ["Year", "Class", "Pos", "BoatName", "SailNo", "TCC", "Skipper",
           "Owner", "BoatType", "Finished", "Elapsed", "Corrected", "Source"]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--out", default="data/rti_islandsc.csv")
    args = p.parse_args()

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    rows = con.execute("""
        SELECT e.season_year, re.class, re.position, re.boat_name_used,
               re.sail_no_used, re.tcc, re.skipper_name_used, re.owner_name_used,
               re.boat_type_used, re.finish_time, re.elapsed_time,
               re.corrected_time
        FROM race_entries re
        JOIN races ra ON ra.id = re.race_id
        JOIN events e ON e.id = ra.event_id
        JOIN regattas rg ON rg.id = e.regatta_id
        WHERE rg.name = ? AND re.source = ?
        ORDER BY e.season_year, re.class, re.position IS NULL, re.position
    """, (REGATTA, SOURCE)).fetchall()
    con.close()

    if not rows:
        raise SystemExit(f"no {SOURCE} entries found for {REGATTA!r} - nothing to write")

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    per_year = {}
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(COLUMNS)
        for (year, cls, pos, name, sail, tcc, skipper, owner, btype,
             finished, elapsed, corrected) in rows:
            # Stored as a bare digit to match event_class_counts; the loader
            # reads "IRC <n>", so write it back in the shape it parses.
            w.writerow([year, f"IRC {cls}", "" if pos is None else pos,
                        name or "", sail or "", "" if tcc is None else tcc,
                        skipper or "", owner or "", btype or "",
                        finished or "", elapsed or "", corrected or "", SOURCE])
            key = (year, cls)
            per_year[key] = per_year.get(key, 0) + 1

    print(f"wrote {len(rows)} row(s) to {out}")
    for (year, cls), n in sorted(per_year.items()):
        print(f"    {year}  IRC {cls}: {n} boats")


if __name__ == "__main__":
    main()
