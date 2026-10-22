#!/usr/bin/env python3
"""Give a boat its type back when the record has none and its entries know it.

625 boats carry a stored type that is not a type at all - "-", "None", blank,
"unknown" - while their own race entries name one, often dozens of times.
SOAK THERAPY is an IMOCA 60 on 56 entries and a "-" on its record; TQUILA is a
Class40 on 23. The type is not missing from the database, only from the field
the dashboard reads.

Filled in ONLY where the entries agree. 228 of the 625 record more than one
type between them, and those are left alone: a boat with two types on its
entries is either a shared sail number or a retype, and both want a person
rather than a majority vote. They are reported so the gap stays visible.

Agreement is judged on a tightened form - case, spaces and punctuation
removed - so "Sun Fast 3300" and "SUNFAST 3300" are one answer, and the
spelling written most often is the one stored.

This is a BACKFILL, not an override: a boat that already has a real type is
never touched, so a hand-made correction cannot be undone by a re-scrape.

    python scripts/backfill_boat_types.py db/marketshare.db
    python scripts/backfill_boat_types.py db/marketshare.db --apply
"""
import argparse, collections, datetime, pathlib, re, shutil, sqlite3

# Values that occupy the field without saying anything.
PLACEHOLDER = {"", "-", "--", "---", "n/a", "na", "none", "null", "unknown",
               "?", "??", "tbc", ".", "x"}


def tight(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    dbp = pathlib.Path(a.db)
    con = sqlite3.connect(dbp)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    rows = cur.execute("""
        SELECT b.id, b.sail_no, b.boat_name, b.boat_type,
               re.boat_type_used AS used, COUNT(*) AS n
          FROM boats b
          JOIN race_entries re ON re.boat_id = b.id
         WHERE re.boat_type_used IS NOT NULL AND TRIM(re.boat_type_used) <> ''
         GROUP BY b.id, re.boat_type_used""").fetchall()

    by_boat = collections.defaultdict(list)
    for r in rows:
        by_boat[r["id"]].append(r)

    fills, split = [], []
    for bid, rs in by_boat.items():
        if (rs[0]["boat_type"] or "").strip().lower() not in PLACEHOLDER:
            continue
        # One answer per tightened form, keeping the commonest spelling of it.
        forms = collections.defaultdict(list)
        for r in rs:
            if (r["used"] or "").strip().lower() in PLACEHOLDER:
                continue
            forms[tight(r["used"])].append(r)
        if not forms:
            continue
        if len(forms) > 1:
            split.append((rs[0]["sail_no"], rs[0]["boat_name"],
                          sorted({r["used"] for r in rs})[:4]))
            continue
        best = max(next(iter(forms.values())), key=lambda r: r["n"])
        fills.append((bid, rs[0]["sail_no"], rs[0]["boat_name"],
                      rs[0]["boat_type"], best["used"], best["n"]))

    print(f"{len(fills)} boat(s) can take a type from their own entries")
    for _, sail, name, was, now, n in sorted(fills, key=lambda x: -x[5])[:12]:
        print(f"   {str(sail):11} {str(name)[:20]:22} {str(was)!r:8} -> {now[:26]:28} x{n}")
    if len(fills) > 12:
        print(f"   ... and {len(fills) - 12} more")

    print(f"\n{len(split)} left alone - their entries name more than one type:")
    for sail, name, types in split[:8]:
        print(f"   {str(sail):11} {str(name)[:20]:22} {' / '.join(str(t)[:18] for t in types)}")
    if len(split) > 8:
        print(f"   ... and {len(split) - 8} more")

    if not a.apply:
        print("\nDRY RUN - pass --apply")
        return

    bak = dbp.with_suffix(f".db.bak-types-{datetime.datetime.now():%H%M%S}")
    shutil.copy2(dbp, bak)
    print("\nbackup ->", bak.name)
    cur.executemany("UPDATE boats SET boat_type = ? WHERE id = ?",
                    [(now, bid) for bid, _, _, _, now, _ in fills])
    con.commit()
    print(f"committed {len(fills)} boat type(s)")


if __name__ == "__main__":
    main()
