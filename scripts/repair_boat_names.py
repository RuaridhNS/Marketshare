#!/usr/bin/env python3
"""
Give a boat record back a name it actually raced under.

boats.boat_name is only ever written from race entries - get_or_create_boat
sets it from the name a loader passes, and nothing else writes it. The IRC
certificate loaders set TCC only, the North Sails market-share sheet does not
contain these names, and apply_boat_updates.py has no name column at all. So a
record whose name matches NONE of its own entries is not carrying a current
name from some other source: it is carrying a name whose entry has since been
moved to another boat or deleted, and the record was never updated.

GBR3750 is the worked example. It is named GLASGOW KISS, but its eight entries
are all GOOD HYDEING from Cowes Week 2017. The real GLASGOW KISS is SGP3750, a
Singapore-registered SB20 holding 47 entries of its own: a source published
that boat under a GBR prefix, its entries were later moved to the right record,
and the name stayed behind on the wrong one. merge_boats.py repairs exactly
this, but only for pairs listed in data/boat_merges.csv - this catches the rest,
including any left behind by a merge made before that repair existed.

ONLY WHERE THE ENTRIES AGREE. A record is renamed when every entry that has a
name uses the same one, which is the case where there is nothing to decide.
Where they disagree the record is reported and left alone, because picking
between them is a judgement rather than a repair: GBR1966L is recorded as
ESSENTIAL SILK and raced as FEMME FATALE, then PREMIER PAPER 3, BONNAY RACING,
FEMME FATALE - ACRISURE and FEMME FATALE LINKLATERS, which are sponsor and
charter names on one hull. Taking the most recent of those - the first version
of this script did - renamed the boat FEMME FATALE LINKLATERS off a single
2025 event, which is worse than what it started with.

CASE FOLDING IS DONE IN PYTHON, NOT SQL. SQLite's UPPER() is ASCII-only, so
UPPER('Ikigaï') is 'IKIGAï' and does not match the stored 'IKIGAÏ'. BEL314
looked like a mismatched record for exactly that reason and is not one - same
name, different case. Comparing with Python's str.upper() drops it from the
report instead of "repairing" a boat whose name was already right.

Boat type and rating are left to normalise_boat_types.py and the certificate
loaders, which own those fields.

Usage:
  python3 repair_boat_names.py <db.sqlite> [--dry-run]
"""
import argparse
import sqlite3


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    # Pulled whole and compared in Python: see the note on SQLite's UPPER().
    names = {}
    for bid, sail, boat_name, used, year in cur.execute("""
            SELECT b.id, b.sail_no, b.boat_name, re.boat_name_used, e.season_year
            FROM boats b
            JOIN race_entries re ON re.boat_id = b.id
            JOIN races ra ON ra.id = re.race_id
            JOIN events e ON e.id = ra.event_id
            WHERE IFNULL(b.boat_name, '') <> '' AND IFNULL(re.boat_name_used, '') <> ''
    """):
        rec = names.setdefault(bid, {"sail": sail, "name": boat_name, "used": {}})
        key = used.upper()
        seen = rec["used"].setdefault(key, {"raw": used, "n": 0, "year": year or 0})
        seen["n"] += 1
        seen["year"] = max(seen["year"], year or 0)

    stale = [(bid, r) for bid, r in names.items()
             if r["name"].upper() not in r["used"]]
    if not stale:
        print("every boat record carries a name one of its own entries used.")
        conn.close()
        return

    fixed = ambiguous = 0
    for bid, r in sorted(stale, key=lambda x: x[1]["sail"] or ""):
        variants = r["used"]
        if len(variants) > 1:
            ambiguous += 1
            raced = ", ".join(f"{v['raw']} ({v['year']})" for v in
                              sorted(variants.values(), key=lambda v: v["year"]))
            print(f"  {r['sail']:12} {r['name']!r} LEFT ALONE - raced under more "
                  f"than one name, so this is a decision: {raced}")
            continue
        only = next(iter(variants.values()))
        if not args.dry_run:
            cur.execute("UPDATE boats SET boat_name = ?, updated_at = datetime('now') "
                        "WHERE id = ?", (only["raw"].upper(), bid))
        print(f"  {r['sail']:12} {r['name']!r} -> {only['raw'].upper()!r}  "
              f"(its only raced name, {only['n']} entr"
              f"{'y' if only['n'] == 1 else 'ies'}, latest {only['year']})")
        fixed += 1

    print(f"\n{fixed} record(s) re-named; {ambiguous} left for a human "
          f"({len(stale)} stale in total)")
    if args.dry_run:
        conn.rollback()
        print("(dry run - nothing written)")
    else:
        conn.commit()
        print("committed")
    conn.close()


if __name__ == "__main__":
    main()
