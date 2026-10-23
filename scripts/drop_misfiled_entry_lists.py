#!/usr/bin/env python3
"""
Drop an entry list that was pasted into the wrong workbook.

The marketshare workbooks are one spreadsheet per event, and a block of rows
can land in the wrong one. The 2025 case that found this: the UK IRC Nationals
entry list - 174 keelboats, Carkeek 40s, TP 52s, a Botin 56, an Oyster 82 -
appears in full inside "Moth Worlds Marketshare 2025.xlsx" AND inside
"470 Worlds Marketshare 2025.xlsx". Every one of the 174 is in all three.

Nothing else catches this. The rows are well-formed, so the column-shift and
superseded-entry passes have no quarrel with them; they are simply filed under
a regatta the boats never sailed. The tell is a Cape 31 in the Moth Worlds.

It surfaced through a boat: KHUMBU, GBR3142, appeared three times in 2025 - at
the 470 Worlds, the Moth Worlds and the IRC Nationals - which made it look like
a second Khumbu record worth merging away. It was one entry triplicated.

THE RULE, deliberately narrow. For a named (regatta, season) and a named
source, an entry is dropped when:
  - it sits on an "Entry List" race, not a result-bearing one, AND
  - the same sail number appears in the SOURCE regatta's entry list for the
    same season.
A row that is only in the wrong workbook is left alone - the point is to
remove a COPY, not to judge whether a boat belongs at an event.

Decisions live in data/misfiled_entry_lists.csv. Run --dry-run first.

Usage:
  python3 drop_misfiled_entry_lists.py <db.sqlite> [--file data/misfiled_entry_lists.csv]
      [--dry-run]
"""
import argparse
import csv
import pathlib
import sqlite3


ENTRY_LIST = "Entry List"


def sails_in(cur, regatta, season):
    """The sail numbers on a regatta's entry list for one season, tightened."""
    return {
        (r[0] or "").replace(" ", "").upper()
        for r in cur.execute(
            """SELECT e.sail_no_used
                 FROM race_entries e
                 JOIN races ra   ON ra.id = e.race_id
                 JOIN events ev  ON ev.id = ra.event_id
                 JOIN regattas rg ON rg.id = ev.regatta_id
                WHERE rg.name = ? AND ev.season_year = ? AND ra.race_name = ?""",
            (regatta, season, ENTRY_LIST))
        if (r[0] or "").strip()
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--file", default="data/misfiled_entry_lists.csv")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    path = pathlib.Path(a.file)
    if not path.exists():
        print(f"{a.file}: not on file, nothing to do")
        return

    con = sqlite3.connect(a.db)
    cur = con.cursor()

    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(
            ln for ln in f if not ln.lstrip().startswith("#")))

    total = 0
    for row in rows:
        regatta = (row.get("Regatta") or "").strip()
        source = (row.get("SourceRegatta") or "").strip()
        season = (row.get("Season") or "").strip()
        if not (regatta and source and season):
            print(f"  incomplete row, skipped: {row!r}")
            continue

        theirs = sails_in(cur, source, season)
        if not theirs:
            print(f"  {source} {season}: no entry list on file - "
                  f"cannot tell a copy from an original, skipped")
            continue

        mine = cur.execute(
            """SELECT e.id, e.sail_no_used, e.boat_name_used, e.boat_type_used
                 FROM race_entries e
                 JOIN races ra   ON ra.id = e.race_id
                 JOIN events ev  ON ev.id = ra.event_id
                 JOIN regattas rg ON rg.id = ev.regatta_id
                WHERE rg.name = ? AND ev.season_year = ? AND ra.race_name = ?""",
            (regatta, season, ENTRY_LIST)).fetchall()

        doomed = [r for r in mine
                  if (r[1] or "").replace(" ", "").upper() in theirs]
        print(f"  {regatta} {season}: {len(doomed)} of {len(mine)} entries are "
              f"copies of the {source} list")
        for r in doomed[:4]:
            print(f"      {str(r[1]):12} {str(r[2])[:20]:22} {str(r[3])[:20]}")
        if len(doomed) > 4:
            print(f"      ... and {len(doomed) - 4} more")

        if doomed and not a.dry_run:
            cur.executemany("DELETE FROM race_entries WHERE id = ?",
                            [(r[0],) for r in doomed])
        total += len(doomed)

    # A boat whose every entry has just gone is a boat that never raced. Left
    # in place deliberately: merge_boats and the duplicate report both read
    # the boats table, and silently deleting records out from under them is
    # how a ledger row stops matching for reasons nobody can reconstruct.
    if not a.dry_run:
        con.commit()
        print(f"\ndropped {total} misfiled entr{'y' if total == 1 else 'ies'}; committed")
    else:
        print(f"\n{total} entr{'y' if total == 1 else 'ies'} would be dropped "
              f"(dry run - nothing written)")


if __name__ == "__main__":
    main()
