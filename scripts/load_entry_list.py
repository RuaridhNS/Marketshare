#!/usr/bin/env python3
"""
Load an event's ENTRY LIST - the boats due to sail a race that has not been
sailed yet.

Every other loader in this project reads results: a finishing position, an
elapsed time, a race that has happened. An entry list is the opposite end of
the same event, and for sales it is the more useful end. These are the boats
that will be on the start line, known weeks in advance, while there is still
time to talk to them.

So an entry list is loaded as a real event with real entries and NO result:
position and elapsed time stay empty, and the race carries status 'scheduled'
until results arrive. Re-running this file after the race, through the normal
results loader, fills those same entries in rather than duplicating them -
races are keyed on (event, race name, class) and entries on (race, boat).

What this deliberately does NOT do is import the sheet's Sailmaker column into
the sailmaker history. That column is the marketing team's own knowledge and
is often better than ours, but it also sometimes disagrees with what we hold,
and silently overwriting a maker on a boat would move the market-share
numbers. The disagreements are reported instead; --import-sailmakers opts in.

Usage:
  python3 load_entry_list.py <db.sqlite> <entry list.xlsx> \
      --regatta "RORC Cherbourg Race" --year 2026 [--sheet 2026]
      [--race-name "Cherbourg Race"] [--category RORC]
      [--import-sailmakers] [--dry-run]
"""
import sys
import argparse
import sqlite3
import pathlib
import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_db import (get_or_create_boat, get_or_create_owner, get_or_create_sailmaker,
                      get_or_create_regatta, get_or_create_event, create_race,
                      norm, norm_upper)

HEADERS = {
    "sail":      ("sail number", "sail no", "sailno"),
    "name":      ("yacht name", "boat name", "name"),
    "owner":     ("owner's name", "owner", "owners name"),
    "sailed_by": ("sailed by", "skipper"),
    "type":      ("type", "boat type", "yacht type"),
    "sailmaker": ("sailmaker", "sail maker"),
    "class":     ("class", "division", "irc class"),
}


def read_sheet(path, sheet=None):
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    # the header is the first row carrying two or more non-empty cells
    h = 0
    while h < len(rows) and sum(1 for c in rows[h] if str(c or "").strip()) < 2:
        h += 1
    head = [str(c or "").strip().lower().replace("\n", " ") for c in rows[h]]
    idx = {}
    for key, names in HEADERS.items():
        for i, col in enumerate(head):
            if any(col.startswith(n) for n in names):
                idx[key] = i
                break
    body = [r for r in rows[h + 1:] if any(str(c or "").strip() for c in r)]
    return wb.sheetnames, idx, body


def cell(row, idx, key):
    i = idx.get(key)
    if i is None or i >= len(row):
        return ""
    return norm(row[i])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("xlsx")
    p.add_argument("--regatta", required=True)
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--sheet")
    p.add_argument("--race-name")
    p.add_argument("--category", default="RORC")
    p.add_argument("--import-sailmakers", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    sheets, idx, body = read_sheet(args.xlsx, args.sheet)
    if "sail" not in idx and "name" not in idx:
        print(f"No sail-number or yacht-name column found. Sheets: {sheets}")
        return
    race_name = args.race_name or args.regatta.replace("RORC ", "").strip()

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    today = datetime.date.today().isoformat()

    regatta_id = get_or_create_regatta(cur, args.regatta, args.category)
    event_id = get_or_create_event(
        cur, regatta_id, args.year,
        notes=f"Entry list loaded {today} - race not yet sailed; entries only, no results.")
    race_id = create_race(cur, event_id, race_name, status="scheduled")

    added = existing = 0
    sm_agree = sm_new = sm_conflict = 0
    conflicts = []

    for row in body:
        sail, name = cell(row, idx, "sail"), cell(row, idx, "name")
        if not sail and not name:
            continue
        boat_id = get_or_create_boat(cur, sail, name, cell(row, idx, "type") or None)
        if not boat_id:
            continue
        owner_id = get_or_create_owner(cur, cell(row, idx, "owner"))

        cur.execute("SELECT id FROM race_entries WHERE race_id = ? AND boat_id = ?",
                    (race_id, boat_id))
        if cur.fetchone():
            existing += 1
        else:
            cur.execute(
                "INSERT INTO race_entries (race_id, boat_id, class, sail_no_used, "
                "boat_name_used, boat_type_used, owner_id, owner_name_used, "
                "skipper_name_used, status, source, scraped_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,'entered','entry-list',?)",
                (race_id, boat_id, cell(row, idx, "class") or None, norm_upper(sail),
                 norm_upper(name), cell(row, idx, "type") or None, owner_id,
                 norm_upper(cell(row, idx, "owner")) or None,
                 norm_upper(cell(row, idx, "sailed_by")) or None,
                 datetime.datetime.now().isoformat()))
            added += 1

        # the sheet's own view of the sailmaker - reported, imported only on request
        stated = cell(row, idx, "sailmaker")
        if stated:
            cur.execute("""SELECT s.name FROM boat_sailmaker_history h
                           JOIN sailmakers s ON s.id = h.sailmaker_id
                           WHERE h.boat_id = ? AND h.confidence != 'partial'
                           ORDER BY h.effective_from DESC LIMIT 1""", (boat_id,))
            got = cur.fetchone()
            ours = got[0] if got else None
            if not ours:
                sm_new += 1
                if args.import_sailmakers:
                    sm_id = get_or_create_sailmaker(cur, stated)
                    if sm_id:
                        cur.execute(
                            "INSERT OR REPLACE INTO boat_sailmaker_history "
                            "(boat_id, sailmaker_id, effective_from, source, confidence) "
                            "VALUES (?,?,?,'ns:entry-list','stated')", (boat_id, sm_id, today))
            elif stated.split()[0].lower() == ours.split()[0].lower():
                sm_agree += 1
            else:
                sm_conflict += 1
                conflicts.append(f"{name or sail}: sheet {stated!r} vs ours {ours!r}")

    print(f"{args.regatta} {args.year} - race {race_name!r} (status scheduled)")
    print(f"  {added} entr{'y' if added == 1 else 'ies'} added, {existing} already present")
    print(f"  sailmaker column: {sm_agree} agree, {sm_new} we do not hold, {sm_conflict} conflict")
    if args.import_sailmakers:
        print(f"  imported {sm_new} stated sailmaker(s); conflicts left alone")
    elif sm_new:
        print(f"  (re-run with --import-sailmakers to take those {sm_new})")
    for c in conflicts[:12]:
        print(f"    conflict: {c}")

    if args.dry_run:
        conn.rollback()
        print("\n(dry run - nothing written)")
    else:
        conn.commit()
        print("\ncommitted")
    conn.close()


if __name__ == "__main__":
    main()
