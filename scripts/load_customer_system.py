#!/usr/bin/env python3
"""
Load a North customer-system export and mark the boats we already sell to.

WHY THIS IS THE ONE THAT MATTERS. Sailmaker is known for 217 of 6,074 boats -
3.6%. Every other source here is exhausted: North's own market-share sheet
names a maker for 121 of its 862 rows and 119 of those are already loaded, the
sail-scan tool gave 12, web research another 13, and race results essentially
never publish it (345 entries in 119,031). Meanwhile boat_crm.in_cs - the "in
the customer system" flag the schema has carried all along - is populated for
exactly zero boats. A customer export is the only input left that could move
coverage by an order of magnitude rather than a dozen boats at a time.

WHAT TO EXPORT. Column names are matched loosely, so most exports work as they
come. The minimum useful shape is one row per customer boat with:

    Sail Number   (or Sail No / SailNo)      - the only reliable key
    Boat Name     (or Yacht Name)            - fallback, and a sanity check
    Owner         (or Customer / Account)    - fallback, and worth having anyway
    Order Date    (or Last Order / Invoice)  - optional, see below
    Product       (or Description / Item)    - optional, see below

WHAT IT WILL AND WILL NOT CLAIM. Being in the customer system is not the same
as flying North sails today: a boat that bought a kite in 2014 and a full
wardrobe elsewhere since is a customer and a competitor's boat at once. So
in_cs is set for every match, and that is a fact. A SAILMAKER is only recorded
when the export carries a date, and then at confidence 'inferred' with that
date as effective_from - the same treatment apply_sailscan_matches.py gives a
sail scan, and for the same reason. --no-sailmaker turns even that off.

Undated rows still set in_cs and are counted, because "we have sold to this
boat, date unknown" is a real and useful thing for a rep to see on the Sales
page, and inventing a date to make it look precise would not be.

Usage:
  python3 load_customer_system.py <db.sqlite> <export.xlsx|.csv> [--dry-run]
      [--no-sailmaker] [--sheet NAME]
"""
import re
import sys
import csv
import argparse
import sqlite3
import pathlib
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_db import get_or_create_sailmaker, norm, norm_upper

COLUMNS = {
    "sail":    re.compile(r"sail\s*(no|number)|^sail$", re.I),
    "name":    re.compile(r"(yacht|boat)\s*name|^name$|^vessel$", re.I),
    "owner":   re.compile(r"owner|customer|account|client", re.I),
    "date":    re.compile(r"date|invoice|ordered|purchase", re.I),
    "product": re.compile(r"product|description|item|sail\s*type|line", re.I),
}
# Same normalisation load_ns_marketshare uses: sources differ only in spacing
# and punctuation, and "GBR 8414R" must find GBR8414R.
def key(s):
    return re.sub(r"[^A-Z0-9]", "", (str(s or "")).upper())


def read_rows(path, sheet=None):
    p = pathlib.Path(path)
    if p.suffix.lower() in (".csv", ".txt"):
        with open(p, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh))
        return rows
    import openpyxl
    wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    return [list(r) for r in ws.iter_rows(values_only=True)]


def find_header(rows):
    """The first row that names a sail number column - exports often carry a
    title row or two above the real header."""
    for i, r in enumerate(rows[:10]):
        cells = [str(c or "") for c in r]
        if any(COLUMNS["sail"].search(c) for c in cells):
            return i, cells
    return 0, [str(c or "") for c in (rows[0] if rows else [])]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("export")
    p.add_argument("--sheet")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-sailmaker", action="store_true",
                   help="set in_cs only; record no sailmaker even where dated")
    args = p.parse_args()

    rows = read_rows(args.export, args.sheet)
    if not rows:
        raise SystemExit("no rows in that file")
    hi, header = find_header(rows)
    idx = {}
    for field, rx in COLUMNS.items():
        for i, h in enumerate(header):
            if h and rx.search(h):
                idx.setdefault(field, i)
    if "sail" not in idx and "name" not in idx:
        raise SystemExit(f"could not find a sail number or boat name column in: {header}")
    print(f"header row {hi + 1}: " + ", ".join(
        f"{f}={header[i]!r}" for f, i in sorted(idx.items())))

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    by_sail, by_name = {}, {}
    for bid, sail, nm in cur.execute("SELECT id, sail_no, boat_name FROM boats"):
        if sail:
            by_sail.setdefault(key(sail), []).append(bid)
        if nm:
            by_name.setdefault(norm_upper(nm), []).append(bid)
    for alias, bid in cur.execute("SELECT alias_sail_no, boat_id FROM boat_sail_aliases"):
        by_sail.setdefault(key(alias), []).append(bid)

    cell = lambda r, f: (str(r[idx[f]]).strip()
                         if f in idx and idx[f] < len(r) and r[idx[f]] is not None else "")
    north_id = get_or_create_sailmaker(cur, "North Sails")
    n_sail = n_name = n_miss = n_sm = 0
    ambiguous, unmatched = [], []
    seen = set()

    for r in rows[hi + 1:]:
        if not any(c not in (None, "") for c in r):
            continue
        sail, nm = cell(r, "sail"), cell(r, "name")
        hits = by_sail.get(key(sail), []) if sail else []
        matched_on = "sail"
        if not hits and nm:
            hits = by_name.get(norm_upper(nm), [])
            matched_on = "name"
        if not hits:
            n_miss += 1
            if len(unmatched) < 12:
                unmatched.append((sail, nm, cell(r, "owner")))
            continue
        if len(set(hits)) > 1:
            # Two boats answer to this - recording either would be a guess.
            if len(ambiguous) < 8:
                ambiguous.append((sail or nm, len(set(hits))))
            continue
        bid = hits[0]
        if bid in seen:
            continue
        seen.add(bid)
        n_sail += matched_on == "sail"
        n_name += matched_on == "name"
        if not args.dry_run:
            cur.execute("INSERT OR IGNORE INTO boat_crm (boat_id) VALUES (?)", (bid,))
            cur.execute("UPDATE boat_crm SET in_cs = 1, last_updated = datetime('now') "
                        "WHERE boat_id = ?", (bid,))
        date = cell(r, "date")
        if date and not args.no_sailmaker:
            iso = None
            m = re.search(r"(\d{4})-(\d{2})-(\d{2})", date)
            if m:
                iso = m.group(0)
            else:
                m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", date)
                if m:
                    iso = f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
            if iso:
                n_sm += 1
                if not args.dry_run:
                    cur.execute(
                        "INSERT OR IGNORE INTO boat_sailmaker_history "
                        "(boat_id, sailmaker_id, effective_from, source, confidence) "
                        "VALUES (?,?,?,?,?)",
                        (bid, north_id, iso, "ns:customer-system", "inferred"))

    total = n_sail + n_name
    print(f"\n{total} boat(s) marked in the customer system "
          f"({n_sail} matched on sail number, {n_name} on name)")
    print(f"{n_sm} sailmaker record(s) written, dated, at confidence 'inferred'"
          + (" (--no-sailmaker)" if args.no_sailmaker else ""))
    if total and not n_sm and not args.no_sailmaker:
        print("  no dated rows, so no sailmaker was claimed - in_cs only. Include an "
              "order or invoice date in the export to get sailmaker history from it.")
    if ambiguous:
        print(f"\n{len(ambiguous)} row(s) matched more than one boat and were skipped "
              f"rather than guessed:")
        for k, n in ambiguous:
            print(f"    {k} -> {n} boats")
    if n_miss:
        print(f"\n{n_miss} row(s) matched no boat here. These are customers not in the "
              f"race data at all - cruising, out of area, or a sail number we have never "
              f"seen - and are worth a look:")
        for s, nm, ow in unmatched:
            print(f"    {s:14} {nm[:26]:28} {ow[:26]}")

    if args.dry_run:
        conn.rollback()
        print("\n(dry run - nothing written)")
    else:
        conn.commit()
        print("\ncommitted")
    conn.close()


if __name__ == "__main__":
    main()
