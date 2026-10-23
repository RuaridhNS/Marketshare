#!/usr/bin/env python3
"""
Undo a boat merge that should never have happened.

merge_boats.py folds one boat record into another, and most of the time that
is right - the same hull entered under a stale sail number, a name change, a
typo. Sometimes it is wrong, and two genuinely different boats end up sharing
one record. That is worse than the duplicate it replaced: every entry, owner
and name from two boats is now attributed to one.

The tell is boat_name_history with confidence 'overlapping' - two names claimed
for the same hull in the same seasons. A boat cannot be called two things at
once, so either the seasons are wrong or the merge was.

This splits one back apart. Entries are assigned by the name they were entered
under, not by sail number, because when two boats are confused it is almost
always the sail number that was mistyped - the name and owner travel together
and are the more reliable pair.

  CSV: FromSailNo,NewSailNo,NewBoatName,NewBoatType,MoveNames[,MoveSails]

MoveNames is a '|'-separated list of boat_name_used values that belong to the
boat being split off. Everything else stays put.

MoveSails is the same list over sail_no_used, for the case the name cannot
decide. Two Cape 31s both called KHUMBU is the example: the 2025 entries were
entered under GBR3142 and the 2026 ones under GBR318X, and the name is the
same word in both - differing only in case, which is house style, not
identity. Matching is done on an upper-cased, space-stripped form, so
'GBR 3412R' and 'gbr3412r' are one value. An entry moves if it matches
MoveNames OR MoveSails; a row needs at least one of the two.

Usage:
  python3 split_boat.py <db.sqlite> <splits.csv> [--dry-run]
"""
import sys
import csv
import argparse
import sqlite3
import pathlib
import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_db import get_or_create_owner, norm, norm_upper


def tight(s):
    """A sail number with its spacing and case taken out, for comparing one
    source's 'GBR 3412R' with another's 'gbr3412r'."""
    return (s or "").replace(" ", "").upper().strip()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("csv_file")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    now = datetime.datetime.now().isoformat()

    with open(args.csv_file, newline="", encoding="utf-8-sig") as f:
        # '#' starts a comment, as in the other ledgers. Without this every
        # line of a block comment came back as "incomplete row, skipped",
        # which buries the rows that are genuinely malformed.
        rows = list(csv.DictReader(ln for ln in f if not ln.lstrip().startswith("#")))

    for row in rows:
        src_sail = norm_upper(row.get("FromSailNo"))
        new_sail = norm_upper(row.get("NewSailNo"))
        new_name = norm_upper(row.get("NewBoatName"))
        new_type = norm(row.get("NewBoatType"))
        move_names = {norm_upper(n) for n in (row.get("MoveNames") or "").split("|") if norm(n)}
        move_sails = {tight(n) for n in (row.get("MoveSails") or "").split("|") if norm(n)}
        if not (src_sail and new_sail and (move_names or move_sails)):
            print("  incomplete row, skipped: %r" % row)
            continue

        # The predicate the whole row turns on, built once: an entry belongs to
        # the boat being split off if its NAME is one of MoveNames or its
        # SAIL NUMBER is one of MoveSails. Sail numbers are compared on the
        # tightened form because sources write 'GBR 3412R' and 'GBR3412R'.
        def selector(negate=False):
            terms, params = [], []
            if move_names:
                terms.append("IFNULL(boat_name_used,'') IN (%s)" % ",".join("?" * len(move_names)))
                params += sorted(move_names)
            if move_sails:
                terms.append("REPLACE(UPPER(IFNULL(sail_no_used,'')),' ','') IN (%s)"
                             % ",".join("?" * len(move_sails)))
                params += sorted(move_sails)
            sql = "(" + " OR ".join(terms) + ")"
            return ("NOT " + sql if negate else sql), params

        got = cur.execute("SELECT id, boat_name FROM boats WHERE sail_no = ?", (src_sail,)).fetchone()
        if not got:
            print("  %s: not on file, skipped" % src_sail)
            continue
        src_id, src_name = got

        # An earlier pass already made this record - or it was always there as
        # a boat of its own. Either way the split is not re-done, but any
        # entries that have landed back on the source since are re-moved: the
        # source that mis-published the sail number is still publishing it, so
        # the next scrape files INNUENDO onto GBR7775R again. Skipping outright
        # made this ledger a one-shot, which is exactly what re-scraping undoes.
        existing = cur.execute("SELECT id FROM boats WHERE sail_no = ?", (new_sail,)).fetchone()
        if existing:
            new_id = existing[0]
            sel, sel_params = selector()
            back = cur.execute(
                "SELECT id FROM race_entries WHERE boat_id = ? AND " + sel,
                [src_id] + sel_params).fetchall()
            if not back:
                print("  %s already split out; nothing has drifted back" % new_sail)
                continue
            print("  %s already split out; %d entr%s drifted back onto %s%s"
                  % (new_sail, len(back), "y" if len(back) == 1 else "ies", src_sail,
                     " (dry run)" if args.dry_run else ""))
            if not args.dry_run:
                cur.executemany("UPDATE race_entries SET boat_id = ? WHERE id = ?",
                                [(new_id, b[0]) for b in back])
                conn.commit()
            continue

        sel, sel_params = selector()
        nsel, nsel_params = selector(negate=True)
        moving = cur.execute(
            "SELECT id FROM race_entries WHERE boat_id = ? AND " + sel,
            [src_id] + sel_params).fetchall()
        staying = cur.execute(
            "SELECT COUNT(*) FROM race_entries WHERE boat_id = ? AND " + nsel,
            [src_id] + nsel_params).fetchone()[0]

        print("\n%s (%s) splits:" % (src_name, src_sail))
        print("    %-14s %s  <- %d entries move" % (new_sail, new_name, len(moving)))
        print("    %-14s stays with %d entries" % (src_sail, staying))
        if not moving:
            print("    nothing matched MoveNames, skipped")
            continue

        if args.dry_run:
            continue

        cur.execute("INSERT INTO boats (sail_no, boat_name, boat_type, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)", (new_sail, new_name, new_type, now, now))
        new_id = cur.lastrowid

        ids = [m[0] for m in moving]
        cur.executemany("UPDATE race_entries SET boat_id = ? WHERE id = ?",
                        [(new_id, i) for i in ids])

        # The alias is what made the sail number resolve to the wrong hull, so
        # it has to go or the next load re-merges them.
        cur.execute("DELETE FROM boat_sail_aliases WHERE alias_sail_no = ? AND boat_id = ?",
                    (new_sail, src_id))

        # Name history: the moved names belong to the new boat. Only when the
        # split was made ON names - a sail-number split leaves it alone,
        # because there the two boats share the name and moving the history
        # would take it off the record that still uses it.
        if move_names:
            cur.execute("UPDATE boat_name_history SET boat_id = ? WHERE boat_id = ? AND name IN (%s)"
                        % ",".join("?" * len(move_names)), [new_id, src_id] + sorted(move_names))
        # What is left no longer overlaps anything, so the flag is stale.
        cur.execute("UPDATE boat_name_history SET confidence = 'inferred' "
                    "WHERE boat_id IN (?, ?) AND confidence = 'overlapping'", (src_id, new_id))

        # The record kept the wrong boat's name when the merge ran, so if the
        # name now living on the source row is one of the ones that just left,
        # take the most recent name its remaining entries were sailed under.
        if move_names and norm_upper(src_name) in move_names:
            keep = cur.execute(
                "SELECT re.boat_name_used, re.boat_type_used FROM race_entries re "
                "JOIN races ra ON ra.id = re.race_id JOIN events e ON e.id = ra.event_id "
                "WHERE re.boat_id = ? AND re.boat_name_used IS NOT NULL "
                "ORDER BY e.season_year DESC LIMIT 1", (src_id,)).fetchone()
            if keep:
                cur.execute("UPDATE boats SET boat_name = ?, boat_type = COALESCE(?, boat_type), "
                            "updated_at = ? WHERE id = ?",
                            (norm_upper(keep[0]), norm(keep[1]), now, src_id))
                print("    %s renamed %s -> %s" % (src_sail, src_name, norm_upper(keep[0])))

        # Owner history is inferred from entries, so recompute both sides from
        # the entries each boat now actually has. Manual rows are left alone -
        # a person decided those and this is not the place to overrule them.
        for bid in (src_id, new_id):
            cur.execute("DELETE FROM boat_owner_history WHERE boat_id = ? "
                        "AND IFNULL(confidence,'') != 'manual'", (bid,))
            seasons = cur.execute(
                "SELECT re.owner_name_used, MIN(e.season_year), MAX(e.season_year) "
                "FROM race_entries re JOIN races ra ON ra.id = re.race_id "
                "JOIN events e ON e.id = ra.event_id "
                "WHERE re.boat_id = ? AND re.owner_name_used IS NOT NULL "
                "GROUP BY re.owner_name_used ORDER BY MIN(e.season_year)", (bid,)).fetchall()
            latest = None
            for owner_name, first, last in seasons:
                oid = get_or_create_owner(cur, owner_name)
                cur.execute("INSERT INTO boat_owner_history "
                            "(boat_id, owner_id, effective_from, effective_to, source, confidence) "
                            "VALUES (?, ?, ?, ?, ?, 'inferred')",
                            (bid, oid, str(first), str(last), "split:boat-split"))
                if latest is None or last >= latest[0]:
                    latest = (last, oid)
            if latest:
                cur.execute("UPDATE boats SET current_owner_id = ?, updated_at = ? WHERE id = ?",
                            (latest[1], now, bid))
            print("    %d: %d owner period(s) rebuilt" % (bid, len(seasons)))

    if args.dry_run:
        conn.rollback()
        print("\n(dry run - nothing written)")
    else:
        conn.commit()
        print("\ncommitted")
    conn.close()


if __name__ == "__main__":
    main()
