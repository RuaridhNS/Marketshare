#!/usr/bin/env python3
"""
Canonicalise boat_type spellings.

The same design arrives from different sources spelled every possible way -
"J/109", "J109", "J 109", "J-109", "J 109 2.10" are all the same boat, and were
five separate entries in the type filter, each matching a different subset. The
J range alone had 73 distinct spellings across 26 designs.

Rules applied, deliberately narrow so it cannot merge genuinely different
designs:
  - strip a trailing IRC rating and configuration suffix ("J 109 2.10",
    "SUN FAST 3300 1.95 WB") - these are rating data, not part of the type
  - J-boats to the builder's own convention: J/<number><lowercase suffix>,
    so J109 / J 109 / J-109 all become J/109, and J/92S becomes J/92s
  - collapse repeated whitespace and trim

Everything else is left alone. Nothing is merged on fuzzy similarity: a change
is only made when the canonical form differs from what is stored, and the
mapping is printed so it can be checked.

Usage:
  python3 normalise_boat_types.py <db.sqlite> [--dry-run]
"""
import re
import argparse
import sqlite3
from collections import Counter

# a trailing rating like "2.10", optionally followed by config text
RATING_TAIL = re.compile(r"\s+[0-2]\.\d{1,2}\b.*$", re.I)
# J-boat: J then optional separator then digits then an optional letter suffix
J_BOAT = re.compile(r"^J\s*[/\-_ ]?\s*(\d{2,3})\s*([A-Za-z])?$", re.I)


def canonical(raw):
    if not raw:
        return raw
    s = re.sub(r"\s+", " ", str(raw)).strip()
    s = RATING_TAIL.sub("", s).strip()
    m = J_BOAT.match(s)
    if m:
        num, suf = m.group(1), (m.group(2) or "")
        return f"J/{num}{suf.lower()}"
    return s


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    rows = cur.execute(
        "SELECT id, boat_type FROM boats WHERE boat_type IS NOT NULL AND TRIM(boat_type) != ''"
    ).fetchall()

    changes = {}
    n = 0
    for bid, raw in rows:
        new = canonical(raw)
        if new and new != raw:
            changes.setdefault((raw, new), 0)
            changes[(raw, new)] += 1
            n += 1
            if not args.dry_run:
                cur.execute("UPDATE boats SET boat_type = ? WHERE id = ?", (new, bid))

    for (old, new), cnt in sorted(changes.items(), key=lambda x: -x[1]):
        print(f"  {old!r:32} -> {new!r:22} ({cnt})")

    # report the J range before/after so the collapse is visible
    after = Counter()
    for _, raw in rows:
        c = canonical(raw)
        if re.match(r"^J/\d", c or ""):
            after[c] += 1
    before = len({raw for _, raw in rows if re.match(r"^\s*J[\s/\-_]?\d", raw or "", re.I)})
    print(f"\n{n} boat(s) retyped.")
    print(f"J-range spellings: {before} distinct -> {len(after)} designs")

    if args.dry_run:
        conn.rollback()
        print("(dry run - nothing written)")
    else:
        conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
