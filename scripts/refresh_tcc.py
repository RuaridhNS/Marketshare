#!/usr/bin/env python3
"""Bring every boat's IRC TCC up to the last rating it actually raced under,
and throw out the numbers that are not TCCs at all.

A boat's TCC is not a fact about the boat, it is a fact about the boat in a
season: new sails, a prop change or an optimisation moves it every year. The
boat record held whatever was written when the boat was first seen, so 615
Solent boats were carrying a rating their own most recent result contradicts.

Two passes, both derived rather than curated - there is nothing here for a
human to decide, so there is no ledger:

  1. Implausible values are cleared. IRC TCCs run from about 0.75 to about
     2.3 (the top of that range is maxi multihulls - Spindrift 2 rates
     2.232). Anything outside 0.5-2.6 is a different number that landed in
     the column: the Cowes Week Folkboat fleet arrived carrying 1081 and
     1092, which are Portsmouth Yardstick numbers for a one-design class
     that has no IRC rating at all.

  2. boats.tcc is set from the most recent season in which the boat has an
     entry carrying a plausible TCC, ignoring ORC classes. ORC is a different
     rating system whose ToT coefficient sits in the same 0.9-1.4 range as an
     IRC TCC and is not one - the giveaway is four decimal places, where IRC
     uses three. The North Sea Race is scored under both, so without this the
     Dutch fleet's ORC numbers would be written into the IRC column.
     Boats with no rated entry anywhere are left alone - most of them are
     one-design or classic boats that are not IRC-rated, and inventing a TCC
     for them would be worse than the gap.

  3. Where a boat has no IRC-rated entry at all and the TCC on its record is
     one of its own ORC coefficients, the record is cleared. That number is
     demonstrably not an IRC rating, and an IRC column is where it was.

If an IRC certificate export is ever wired into the pipeline, it is better
evidence than a results page and should run AFTER this script, not before.

    python scripts/refresh_tcc.py db/marketshare.db
    python scripts/refresh_tcc.py db/marketshare.db --apply
"""
import argparse, datetime, pathlib, shutil, sqlite3

LO, HI = 0.5, 2.6
# Exactly 1.000 and exactly 2.000 are placeholders, not ratings. 1.0 is on 688
# entries across 80 boats and 2.0 on 65 across 15, sprayed through the Royal
# Southern regattas and the Warsash championships - a J/112e does not rate 2.0,
# and eighty boats do not all rate exactly 1.000. A real rating that lands on
# either is lost with them, which costs less than a default read as a fact.
SENTINELS = (1.0, 2.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    dbp = pathlib.Path(a.db)
    if a.apply:
        bak = dbp.with_suffix(f".db.bak-tcc-{datetime.datetime.now():%H%M%S}")
        shutil.copy2(dbp, bak)
        print("backup ->", bak.name)
    con = sqlite3.connect(dbp)
    cur = con.cursor()

    has_certs = bool(cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                 "AND name='boat_irc_certs'").fetchone())

    # ---- 1. values that are not TCCs ------------------------------------
    bad = "(tcc < ? OR tcc > ? OR tcc IN (%s))" % ",".join("?" * len(SENTINELS))
    bad_args = (LO, HI, *SENTINELS)
    # A sentinel is only a sentinel on a results page. Exactly 1.000 off the
    # Rating Office is a rating: BLUE JAY ran 1.010, 1.005, 1.003, 1.002 and
    # then 1.000, and eighteen boats like it were being cleared here and
    # restored by the certificate loader on every run. A value a certificate
    # states is never "not a TCC".
    certified = (" AND NOT EXISTS (SELECT 1 FROM boat_irc_certs ic "
                 "WHERE ic.boat_id = boats.id AND ABS(ic.tcc - boats.tcc) <= 0.0005)"
                 if has_certs else "")
    bad_entries = cur.execute(
        f"SELECT COUNT(*) FROM race_entries WHERE tcc IS NOT NULL AND {bad}",
        bad_args).fetchone()[0]
    bad_boats = cur.execute(
        f"SELECT COUNT(*) FROM boats WHERE tcc IS NOT NULL AND {bad}{certified}",
        bad_args).fetchone()[0]
    print(f"not a TCC: {bad_entries} entr(y/ies), {bad_boats} boat record(s)")
    for tcc, n, cls in cur.execute(
            f"SELECT tcc, COUNT(*), GROUP_CONCAT(DISTINCT class) FROM race_entries "
            f"WHERE tcc IS NOT NULL AND {bad} GROUP BY tcc "
            f"ORDER BY COUNT(*) DESC LIMIT 10", bad_args):
        print(f"   {tcc:<10} {n:4} entr(y/ies)   class: {str(cls)[:40]}")

    # ---- 2. the rating each boat last raced under -----------------------
    # A boat holding a certificate is not this script's business: it fills the
    # gap where the Rating Office has told us nothing. Left to compete, it
    # overwrote 1,023 certificate ratings on every run and
    # load_irc_tcc_listing.py put every one of them back. Guarded on the
    # table's existence so a database built before the certificates arrived
    # still runs.
    cert_guard = ("AND NOT EXISTS (SELECT 1 FROM boat_irc_certs ic WHERE ic.boat_id = b.id)"
                  if has_certs else "")
    rows = cur.execute(f"""
        WITH latest AS (
          SELECT re.boat_id, re.tcc, e.season_year,
                 ROW_NUMBER() OVER (PARTITION BY re.boat_id
                                    ORDER BY e.season_year DESC, re.id DESC) rn
            FROM race_entries re
            JOIN races ra ON ra.id = re.race_id
            JOIN events e  ON e.id  = ra.event_id
           WHERE re.tcc IS NOT NULL AND re.tcc BETWEEN ? AND ?
             AND re.tcc NOT IN (?,?)
             AND e.season_year IS NOT NULL
             AND COALESCE(re.class, '') NOT LIKE '%ORC%')
        SELECT l.boat_id, b.boat_name, b.sail_no, b.tcc, l.tcc, l.season_year
          FROM latest l JOIN boats b ON b.id = l.boat_id
         WHERE l.rn = 1
           AND (b.tcc IS NULL OR ABS(b.tcc - l.tcc) > 0.0005)
           {cert_guard}
         ORDER BY l.season_year DESC, ABS(COALESCE(b.tcc, 0) - l.tcc) DESC
    """, (LO, HI, *SENTINELS)).fetchall()

    # ---- 3. ORC coefficients sitting in the IRC column ------------------
    orc_only = cur.execute("""
        SELECT b.id, b.boat_name, b.sail_no, b.tcc
          FROM boats b
         WHERE b.tcc IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM race_entries re
                            WHERE re.boat_id = b.id AND re.tcc IS NOT NULL
                              AND COALESCE(re.class, '') NOT LIKE '%ORC%')
           AND EXISTS (SELECT 1 FROM race_entries re
                        WHERE re.boat_id = b.id AND re.class LIKE '%ORC%'
                          AND ABS(re.tcc - b.tcc) <= 0.0005)
    """).fetchall()
    print(f"\nORC coefficients in the IRC column: {len(orc_only)} boat(s) to clear")
    for _, name, sail, tcc in orc_only[:6]:
        print(f"   {tcc:<8} {str(name or sail)[:34]}")

    filled = sum(1 for r in rows if r[3] is None)
    print(f"\nboats to update: {len(rows)}  ({filled} with no TCC at all, "
          f"{len(rows) - filled} carrying one their last result contradicts)")
    for _, name, sail, old, new, yr in rows[:12]:
        print(f"   {str(old or '-'):>6} -> {new:<6} {yr}  {str(name or sail)[:34]}")
    if len(rows) > 12:
        print(f"   ... and {len(rows) - 12} more")

    if not a.apply:
        print("\nDRY RUN - pass --apply")
        return

    cur.execute(f"UPDATE race_entries SET tcc = NULL WHERE tcc IS NOT NULL AND {bad}", bad_args)
    cur.execute(f"UPDATE boats SET tcc = NULL WHERE tcc IS NOT NULL AND {bad}{certified}",
                bad_args)
    cur.executemany("UPDATE boats SET tcc = ? WHERE id = ?",
                    [(r[4], r[0]) for r in rows])
    cur.executemany("UPDATE boats SET tcc = NULL WHERE id = ?",
                    [(r[0],) for r in orc_only])
    con.commit()
    print(f"\ncleared {bad_entries + bad_boats} impossible value(s) and "
          f"{len(orc_only)} ORC coefficient(s); updated {len(rows)} boat rating(s)")


if __name__ == "__main__":
    main()
