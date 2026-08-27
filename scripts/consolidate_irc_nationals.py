#!/usr/bin/env python3
"""
Consolidate the IRC National Championship records.

The Nationals had fragmented into eleven separate "regattas" through a mix of
naming drift across seasons and one-regatta-per-class scraping:

    IRC Nationals                                (aggregate counts only)
    IRC National Championship                    2012-2014
    IRC National Championship -                  2015-2017
    IRC National Championship - Fast40+          2017
    IRC National Championships -                 2019-2022
    IRC National Championships - Fast40+         2019
    IRC National Championships - HP30            2019
    IRC National Championships - IRC 2           2019-2022
    IRC National Championships - IRC 3           2019-2022
    IRC National Championships - Performance40   2019

They are one event. The Two-Handed Nationals are a genuinely different event
that had fragmented the same way, so it is consolidated separately rather than
folded in.

The catch: on the per-class records the entries carry NO class of their own -
the division exists only in the regatta name. Merging first and asking
questions later would silently destroy it, so the suffix is written onto the
entries as their class BEFORE anything is merged. Those divisions are all
IRC-rated fleets racing at an IRC championship, so they are labelled "IRC ..."
to reflect that (and to survive the export's IRC-only entry filter).

Usage:
  python3 consolidate_irc_nationals.py <db.sqlite> [--dry-run]
"""
import argparse
import sqlite3

# regatta name -> class to stamp on its (class-less) entries
SUFFIX_CLASS = {
    "IRC National Championships - IRC 2": "IRC 2",
    "IRC National Championships - IRC 3": "IRC 3",
    "IRC National Championships - Fast40+": "IRC Fast 40+",
    "IRC National Championship - Fast40+": "IRC Fast 40+",
    "IRC National Championships - HP30": "IRC HP30",
    "IRC National Championships - Performance40": "IRC Performance 40",
}

GROUPS = {
    "IRC National Championship": [
        "IRC Nationals",
        "IRC National Championship",
        "IRC National Championship -",
        "IRC National Championship - Fast40+",
        "IRC National Championships -",
        "IRC National Championships - Fast40+",
        "IRC National Championships - HP30",
        "IRC National Championships - IRC 2",
        "IRC National Championships - IRC 3",
        "IRC National Championships - Performance40",
    ],
    "IRC Two-Handed National Championship": [
        "IRC Two-Handed National Championship",
        "IRC Two-Handed Nationals",
        "IRC Double Handed National Championships",
    ],
}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    # 1. preserve the division that only exists in the regatta name
    stamped = 0
    for name, cls in SUFFIX_CLASS.items():
        row = cur.execute("SELECT id FROM regattas WHERE name = ?", (name,)).fetchone()
        if not row:
            continue
        n = cur.execute("""
            SELECT COUNT(*) FROM race_entries WHERE class IS NULL AND race_id IN (
                SELECT ra.id FROM races ra JOIN events e ON e.id = ra.event_id
                WHERE e.regatta_id = ?)""", (row[0],)).fetchone()[0]
        if n and not args.dry_run:
            cur.execute("""
                UPDATE race_entries SET class = ? WHERE class IS NULL AND race_id IN (
                    SELECT ra.id FROM races ra JOIN events e ON e.id = ra.event_id
                    WHERE e.regatta_id = ?)""", (cls, row[0]))
        if n:
            print(f"  stamped class {cls!r} on {n} entries of {name!r}")
            stamped += n

    # 2. merge each group onto one canonical regatta
    merged_ev = dropped_ev = dropped_reg = 0
    for canon, names in GROUPS.items():
        ids = {}
        for nm in names:
            r = cur.execute("SELECT id FROM regattas WHERE name = ?", (nm,)).fetchone()
            if r:
                ids[nm] = r[0]
        if not ids:
            continue
        keep_name = canon if canon in ids else next(iter(ids))
        keep = ids[keep_name]
        if not args.dry_run:
            cur.execute("UPDATE regattas SET name = ? WHERE id = ?", (canon, keep))
        print(f"\n{canon}: keeping [{keep}], absorbing {len(ids)-1} other record(s)")

        for nm, rid in ids.items():
            if rid == keep:
                continue
            for eid, yr in cur.execute(
                    "SELECT id, season_year FROM events WHERE regatta_id = ?", (rid,)).fetchall():
                tgt = cur.execute("SELECT id FROM events WHERE regatta_id = ? AND season_year = ?",
                                  (keep, yr)).fetchone()
                if tgt:
                    if not args.dry_run:
                        cur.execute("UPDATE races SET event_id = ? WHERE event_id = ?", (tgt[0], eid))
                        # class-count rows can collide on (event, label, source)
                        cur.execute("""UPDATE OR IGNORE event_class_counts SET event_id = ?
                                       WHERE event_id = ?""", (tgt[0], eid))
                        cur.execute("DELETE FROM event_class_counts WHERE event_id = ?", (eid,))
                        cur.execute("DELETE FROM events WHERE id = ?", (eid,))
                    dropped_ev += 1
                else:
                    if not args.dry_run:
                        cur.execute("UPDATE events SET regatta_id = ? WHERE id = ?", (keep, eid))
                    merged_ev += 1
            if not args.dry_run:
                cur.execute("DELETE FROM regattas WHERE id = ?", (rid,))
            dropped_reg += 1
            print(f"    absorbed {nm!r}")

    if args.dry_run:
        conn.rollback()
        print("\n(dry run - nothing written)")
    else:
        conn.commit()
    print(f"\nStamped {stamped} entries; re-pointed {merged_ev} event(s); "
          f"merged {dropped_ev} same-year event(s); removed {dropped_reg} regatta record(s).")
    conn.close()


if __name__ == "__main__":
    main()
