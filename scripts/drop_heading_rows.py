#!/usr/bin/env python3
"""
Drop the spreadsheet heading rows that were imported as boats.

A marketshare workbook groups its entries under division headings, and some of
those headings came through the import as rows of their own. They are now boat
records:

    IRC | Division Total: 53
    Daring | Division Total: 11
    J/70's  | Division Total: 29
    ORC | ORC Overall
    IRC | Class not assigned

Seventeen such records across five events, each with one or two entries. They
inflate a fleet by a boat that is actually a subtotal OF that fleet, and they
turn up in the duplicate report as records with no name and no type, where
someone has to work out what they are before dismissing them.

THE TEST IS THE PIPE. A sail number is issued by a rating authority and does
not contain one; a spreadsheet heading that has been flattened from two cells
into one does. Every one of the twenty distinct pipe-bearing values in this
database is a heading, and nothing else in it has a pipe at all - which is why
this can be a rule rather than a ledger. Checked before it was written, and
--dry-run prints every row so it can be checked again.

A boat record left with no entries is deleted too, but only if it holds
nothing else - no owner history, no name history, no aliases. There is nothing
to keep about a subtotal.

Usage:
  python3 drop_heading_rows.py <db.sqlite> [--dry-run]
"""
import argparse
import sqlite3

# Flattened two-cell headings; no sail number anywhere carries a pipe.
HEADING = "%|%"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    con = sqlite3.connect(a.db)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    entries = cur.execute(
        """SELECT e.id, e.boat_id, e.sail_no_used, rg.name AS regatta, ev.season_year
             FROM race_entries e
             JOIN races ra    ON ra.id = e.race_id
             JOIN events ev   ON ev.id = ra.event_id
             JOIN regattas rg ON rg.id = ev.regatta_id
            WHERE e.sail_no_used LIKE ?""", (HEADING,)).fetchall()

    if not entries:
        print("no heading rows on file")
        return

    print(f"{len(entries)} entr(y/ies) whose sail number is a spreadsheet heading:")
    for r in entries:
        print(f"    {r['season_year']} {str(r['regatta'])[:30]:32} {r['sail_no_used']}")

    # Boats that exist only because of these rows.
    boat_ids = {r["boat_id"] for r in entries if r["boat_id"]}
    orphans = []
    for bid in sorted(boat_ids):
        others = cur.execute(
            "SELECT COUNT(*) FROM race_entries WHERE boat_id = ? AND sail_no_used NOT LIKE ?",
            (bid, HEADING)).fetchone()[0]
        if others:
            continue
        # Only if the record carries nothing a person put there.
        extra = sum(cur.execute(f"SELECT COUNT(*) FROM {t} WHERE boat_id = ?",
                                (bid,)).fetchone()[0]
                    for t in ("boat_owner_history", "boat_name_history",
                              "boat_sail_aliases", "boat_sailmaker_history"))
        orphans.append((bid, extra))

    clean = [b for b, extra in orphans if not extra]
    kept = [b for b, extra in orphans if extra]
    print(f"\n{len(clean)} boat record(s) exist only for these rows and will go")
    if kept:
        print(f"{len(kept)} left in place - they carry owner or name history "
              f"somebody may have put there: {kept}")

    if a.dry_run:
        print("\n(dry run - nothing written)")
        return

    cur.executemany("DELETE FROM race_entries WHERE id = ?",
                    [(r["id"],) for r in entries])
    if clean:
        cur.executemany("DELETE FROM boats WHERE id = ?", [(b,) for b in clean])
    con.commit()
    print(f"\ndropped {len(entries)} entr(y/ies) and {len(clean)} boat record(s); committed")


if __name__ == "__main__":
    main()
