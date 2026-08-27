#!/usr/bin/env python3
"""
Move a named race out of RORC Mainseries into its own regatta.

The RORC legacy scraper files every race of a season under one "RORC
Mainseries" event, so the Rolex Fastnet Race and the Myth of Malham - both
flagship regattas in their own right, and both already present as
aggregate-only regattas imported from the IRC Solent Report - had their
boat-level results buried inside Mainseries. Each event's history was split
in half: aggregate entry totals on one record, the actual boats on another.

This moves the races to the standalone regatta, matching on season year, so a
regatta page shows its aggregate history and its boat-level racing together
(the same fix already applied to the RSYC / Royal Southern duplication).

Races are matched by name against the target regatta, across every regatta
whose name starts with the given source prefix. Safe to re-run: once a race
has moved, it is no longer in a source regatta.

Usage:
  python3 split_out_named_races.py <db.sqlite> [--dry-run]
"""
import argparse
import sqlite3

# target regatta name -> LIKE pattern matching its races inside the source
MOVES = {
    "Rolex Fastnet Race": "%Fastnet%",
    "RORC Myth of Malham": "%Myth of Malham%",
}
SOURCE_PREFIXES = ("RORC Mainseries", "IRC Two-Handed")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    for target, pattern in MOVES.items():
        row = cur.execute("SELECT id FROM regattas WHERE name = ?", (target,)).fetchone()
        if not row:
            print(f"  {target!r}: no such regatta, skipping")
            continue
        tgt_id = row[0]

        srcs = []
        for pref in SOURCE_PREFIXES:
            srcs += [r[0] for r in cur.execute(
                "SELECT id FROM regattas WHERE name LIKE ? AND id != ?",
                (pref + "%", tgt_id)).fetchall()]
        if not srcs:
            continue
        q = ",".join("?" * len(srcs))

        races = cur.execute(f"""
            SELECT ra.id, ra.race_name, e.season_year
            FROM races ra JOIN events e ON e.id = ra.event_id
            WHERE e.regatta_id IN ({q}) AND ra.race_name LIKE ?
            ORDER BY e.season_year, ra.id""", (*srcs, pattern)).fetchall()
        if not races:
            print(f"  {target!r}: nothing to move")
            continue

        moved, by_year = 0, {}
        for rid, rname, year in races:
            tev = cur.execute(
                "SELECT id FROM events WHERE regatta_id = ? AND season_year = ?",
                (tgt_id, year)).fetchone()
            if tev:
                tev = tev[0]
            else:
                if args.dry_run:
                    tev = -1
                else:
                    cur.execute(
                        "INSERT INTO events (regatta_id, season_year) VALUES (?, ?)",
                        (tgt_id, year))
                    tev = cur.lastrowid
            if not args.dry_run:
                cur.execute("UPDATE races SET event_id = ? WHERE id = ?", (tev, rid))
            moved += 1
            by_year[year] = by_year.get(year, 0) + 1

        n_entries = cur.execute(f"""
            SELECT COUNT(*) FROM race_entries WHERE race_id IN
              ({",".join(str(r[0]) for r in races)})""").fetchone()[0]
        print(f"  {target!r}: moved {moved} race(s), {n_entries} entries "
              f"-> years {sorted(by_year)}")

    # drop any Mainseries events left with no races at all
    empties = cur.execute("""
        SELECT e.id FROM events e
        LEFT JOIN races ra ON ra.event_id = e.id
        WHERE ra.id IS NULL
          AND NOT EXISTS (SELECT 1 FROM event_class_counts cc WHERE cc.event_id = e.id)
    """).fetchall()
    if empties and not args.dry_run:
        cur.executemany("DELETE FROM events WHERE id = ?", empties)
    print(f"  removed {len(empties)} now-empty event(s)")

    if args.dry_run:
        conn.rollback()
        print("(dry run - nothing written)")
    else:
        conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
