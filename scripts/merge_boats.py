#!/usr/bin/env python3
"""
Fold one boat record into another, or rescue entries filed onto the wrong boat.

Cape 31 sail numbers carry an X - GBR3113X, GBR314X, GBR3110X - and the Royal
Southern June Regatta publishes them without it. Two different things then
happen, and they need different treatment:

  A PHANTOM. Nothing else owns the bare number, so a second boat record is
  created. GBR3110 and GBR314R are JUBILEE and KATABATIC a second time, same
  owner, same class. Fold the whole record in.

  A COLLISION. Another boat already owns the bare number, so the entries land
  on IT. GBR3113 is ECLIPSE, a 0.924-rated boat that raced IRC 4 in 2018; six
  2026 entries for SWIFT HALF, a Cape 31, were filed onto it. Folding the whole
  record would merge two genuinely different boats. Only the wrongly-filed
  entries move, named by the boat name they were recorded under.

That is what OnlyNamed is for: blank folds the whole record, a name moves just
the entries recorded under it and leaves the rest where they are.

Decisions live in data/boat_merges.csv. Run --dry-run first; it prints what
each row would move before anything is written.

Usage:
  python3 merge_boats.py <db.sqlite> [--file data/boat_merges.csv] [--dry-run]
"""
import csv
import argparse
import sqlite3
import pathlib

# tables that point at a boat, and the columns that make a row unique there
CARRIED = [("race_entries", "boat_id", ["race_id"]),
           ("boat_owner_history", "boat_id", ["owner_id", "effective_from"]),
           ("boat_sailmaker_history", "boat_id", ["sailmaker_id", "effective_from"]),
           ("boat_name_history", "boat_id", ["name", "effective_from"]),
           ("boat_sail_aliases", "boat_id", ["alias_sail_no"])]


def table_exists(cur, name):
    return cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                       (name,)).fetchone() is not None


def move_entries(cur, src, dst, only_named, dry):
    """Move race entries, skipping any the destination already has for that race."""
    rows = cur.execute(
        "SELECT id, race_id FROM race_entries WHERE boat_id = ?" +
        (" AND UPPER(boat_name_used) = UPPER(?)" if only_named else ""),
        (src, only_named) if only_named else (src,)).fetchall()
    moved = dropped = 0
    for eid, race_id in rows:
        clash = cur.execute("SELECT 1 FROM race_entries WHERE race_id = ? AND boat_id = ?",
                            (race_id, dst)).fetchone()
        if clash:
            if not dry:
                cur.execute("DELETE FROM race_entries WHERE id = ?", (eid,))
            dropped += 1
        else:
            if not dry:
                cur.execute("UPDATE race_entries SET boat_id = ? WHERE id = ?", (dst, eid))
            moved += 1
    return moved, dropped


def name_clash(cur, dst, src):
    """Seasons where the two records raced under different names.

    A hull has one name in a given season. If these two were the same boat,
    every season they share would show the same name on both sides - so a
    season where one is ESCAPADO and the other is NORTH STAR means they are two
    boats, not one record duplicated.

    This is not a theoretical worry. GBR7557 was merged into GBR7775R on a name
    match; they were two different Quarter Tonners, the merged record ended up
    carrying one boat's sail number with the other's name, and a later load
    silently overwrote a real result because both now resolved to one boat.
    The overlap was visible in boat_name_history the whole time.
    """
    def by_season(bid):
        d = {}
        for nm, yr in cur.execute(
                "SELECT UPPER(IFNULL(re.boat_name_used,'?')), e.season_year "
                "FROM race_entries re JOIN races ra ON ra.id = re.race_id "
                "JOIN events e ON e.id = ra.event_id WHERE re.boat_id = ?", (bid,)):
            d.setdefault(yr, set()).add(nm)
        return d
    a, b = by_season(dst), by_season(src)
    return [(y, sorted(a[y]), sorted(b[y]))
            for y in sorted(set(a) & set(b)) if not (a[y] & b[y])]


def rederive_identity(cur, bid, sail, dry):
    """Give a partially-merged record back its own identity.

    A partial move takes the entries that belonged to the other boat and leaves
    the rest - but the boats row keeps whatever name, type and rating it had,
    and after a collision that is the DEPARTED boat's. GBR7017R sat as
    VENOMOUS, a Carroll Marine 60 rating 1.333, while all twelve entries it
    still held were BLACK PEARL, a Botin 56 rating 1.46. GBR918R was JARHEAD
    holding four OLWYN entries. GBR3113 was SWIFT HALF, a Cape 31, holding
    four ECLIPSE entries rated 0.924.

    That state is worse than cosmetic. The record cannot be found by its real
    name, a loader matching on name will not resolve to it - and the duplicate
    detector reads exactly these two fields, so a record left wearing the other
    boat's name and type is what gets proposed for the same bad merge next
    time. The fix for a bad merge has to clean up after itself or it feeds one.

    Fires only when the stored name appears in NONE of the remaining entries,
    which is the same test as the audit that found these three. A spelling
    variant (WATERMARK II holding WATERMARK 2) or a real rename is left alone -
    those need a human call about which name is current, and this is not it.
    """
    row = cur.execute("SELECT boat_name, boat_type, tcc FROM boats WHERE id = ?", (bid,)).fetchone()
    if not row:
        return
    name, btype, tcc = row
    if not name:
        return
    left = cur.execute(
        "SELECT UPPER(IFNULL(re.boat_name_used,'')), IFNULL(re.boat_type_used,''), re.tcc "
        "FROM race_entries re JOIN races ra ON ra.id = re.race_id "
        "JOIN events e ON e.id = ra.event_id WHERE re.boat_id = ? "
        "ORDER BY IFNULL(e.season_year, 0) DESC", (bid,)).fetchall()
    if not left or any(n == name.upper() for n, _, _ in left):
        return

    new_name = next((n for n, _, _ in left if n), None)
    if not new_name:
        return
    # Most recent non-empty wins for type and rating. Where nothing remaining
    # records a type, NULL is the honest answer and not the old one: keeping
    # "Cape 31" on the boat that is not a Cape 31 is precisely what would let
    # the detector pair it with the real one again.
    new_type = next((t for _, t, _ in left if t), None)
    new_tcc = next((c for _, _, c in left if c), None)
    if not dry:
        cur.execute("UPDATE boats SET boat_name = ?, boat_type = ?, tcc = ? WHERE id = ?",
                    (new_name, new_type, new_tcc, bid))
    print(f"    {sail} re-identified: {name!r} [{btype or '?'}] tcc={tcc} "
          f"-> {new_name!r} [{new_type or '?'}] tcc={new_tcc}"
          f"{' (dry run)' if dry else ''}")


def shares_nothing(cur, dst, src):
    """True when the two records share neither a raced name nor an owner.

    name_clash only refuses a pair that raced in the SAME season under
    different names. Two different boats whose seasons happen not to overlap
    sail straight past it, and the Duplicates page proposes exactly those: its
    sail-number rule pairs anything differing by a trailing letter with a
    matching type, which is thin evidence on its own.

    Two got through that way. GBR5940 is Roger Bowden's King 40 NIFTY, raced in
    2018; GBR5940R is Michael Bartholomew's King 40 TOKOLOSHE, 55 entries from
    2009 to 2019 - and the merge would have folded TOKOLOSHE into NIFTY.
    GBR1445 is Agne V Nilsson's Farr 45 FORTIS EXCEL; GBR1445R is Jonathan
    Bamberger's SPITFIRE. Same type, one letter apart, no season in common, no
    name in common, no owner in common.

    A shared owner is what separates these from a legitimate sponsor rename:
    GBR4601 and GBR4601L are both John Shepherd's FAIR DO'S VII, one of them
    entered as THREADNEEDLE ASSET MANAGEMENT, and they share no name at all -
    only the owner says they are one boat. So this refuses only when BOTH
    signals are absent.
    """
    def names_and_owners(bid):
        names, owners = set(), set()
        for nm, ow in cur.execute(
                "SELECT UPPER(IFNULL(boat_name_used,'')), UPPER(IFNULL(owner_name_used,'')) "
                "FROM race_entries WHERE boat_id = ?", (bid,)):
            if nm:
                names.add(nm)
            if ow:
                owners.add(ow)
        return names, owners
    an, ao = names_and_owners(dst)
    bn, bo = names_and_owners(src)
    if not (an and bn):
        return None            # one side has no named entry: nothing to judge on
    if (an & bn) or (ao & bo):
        return None
    return (sorted(an)[:3], sorted(ao)[:2], sorted(bn)[:3], sorted(bo)[:2])


def fold(cur, keep_sail, fold_sail, only_named, dry, force=False):
    k = cur.execute("SELECT id, boat_name FROM boats WHERE sail_no = ?", (keep_sail,)).fetchone()
    f = cur.execute("SELECT id, boat_name FROM boats WHERE sail_no = ?", (fold_sail,)).fetchone()
    if not k or not f or k[0] == f[0]:
        print(f"  skip {fold_sail!r} -> {keep_sail!r}: not found or same record")
        return 0
    dst, src = k[0], f[0]

    clashes = name_clash(cur, dst, src)
    if clashes and not only_named and not force:
        print(f"  REFUSED {fold_sail} {f[1]!r} -> {keep_sail} {k[1]!r}: "
              f"both raced in the same season under different names, so these look "
              f"like two boats:")
        for yr, an, bn in clashes:
            print(f"      {yr}: {keep_sail} as {'/'.join(an)}, {fold_sail} as {'/'.join(bn)}")
        print("      Re-run with --force if you know they are the same hull.")
        return 0

    apart = shares_nothing(cur, dst, src) if not only_named and not force else None
    if apart:
        an, ao, bn, bo = apart
        print(f"  REFUSED {fold_sail} {f[1]!r} -> {keep_sail} {k[1]!r}: they share no "
              f"raced name and no owner, so nothing says they are one boat:")
        print(f"      {keep_sail:12} raced as {'/'.join(an)}  owner(s) {'/'.join(ao) or '?'}")
        print(f"      {fold_sail:12} raced as {'/'.join(bn)}  owner(s) {'/'.join(bo) or '?'}")
        print("      Re-run with --force, or set OnlyNamed, if you know better.")
        return 0

    moved, dropped = move_entries(cur, src, dst, only_named, dry)
    note = f" (only entries named {only_named!r})" if only_named else ""
    print(f"  {fold_sail} {f[1]!r} -> {keep_sail} {k[1]!r}{note}: "
          f"{moved} entr{'y' if moved == 1 else 'ies'} moved, {dropped} already present")

    if only_named:
        left = cur.execute("SELECT COUNT(*) FROM race_entries WHERE boat_id = ?", (src,)).fetchone()[0]
        # A dry run has written nothing, so the stored count is still the
        # pre-move total - it reported GBR918R keeping all 17 of its entries
        # when 13 were about to leave. "What stays behind" is the whole point of
        # checking a partial move before committing it, so take the moved and
        # dropped rows off by hand.
        if dry:
            left -= moved + dropped
        print(f"    {fold_sail} keeps its own {left} entr{'y' if left == 1 else 'ies'}")
        # Runs whether or not anything moved this time, so re-running the
        # ledger repairs a record left mis-identified by an earlier partial
        # merge instead of only catching the ones being applied today.
        rederive_identity(cur, src, fold_sail, dry)
        return 1

    # whole-record fold: carry the history across, then drop the empty record
    for table, col, keys in CARRIED[1:]:
        if not table_exists(cur, table):
            continue
        if not dry:
            cur.execute(f"UPDATE OR IGNORE {table} SET {col} = ? WHERE {col} = ?", (dst, src))
            cur.execute(f"DELETE FROM {table} WHERE {col} = ?", (src,))
    if not dry:
        if table_exists(cur, "boat_crm"):
            cur.execute("DELETE FROM boat_crm WHERE boat_id = ?", (src,))
        # keep the fuller sail number findable
        if table_exists(cur, "boat_sail_aliases"):
            cur.execute("INSERT OR IGNORE INTO boat_sail_aliases (boat_id, alias_sail_no) VALUES (?,?)",
                        (dst, fold_sail))
        cur.execute("DELETE FROM boats WHERE id = ?", (src,))
    print(f"    {fold_sail} record removed; its sail number kept as an alias of {keep_sail}")
    return 1


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--file", default="data/boat_merges.csv")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="merge even when the two records raced under different "
                        "names in the same season")
    args = p.parse_args()

    path = pathlib.Path(args.file)
    if not path.exists():
        print(f"no decisions file at {path}")
        return
    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    n = 0
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            # The Duplicates page exports KeepSailNo/MergeSailNo; this script
            # was written for Keep/Fold. Reading both means a file copied
            # straight out of the dashboard just works, instead of silently
            # matching nothing and reporting "0 records processed".
            keep = (row.get("Keep") or row.get("KeepSailNo") or "").strip()
            fld = (row.get("Fold") or row.get("MergeSailNo") or "").strip()
            only = (row.get("OnlyNamed") or "").strip()
            if keep and fld:
                n += fold(cur, keep, fld, only, args.dry_run, args.force)
    print(f"\n{n} record(s) processed")
    if args.dry_run:
        conn.rollback()
        print("(dry run - nothing written)")
    else:
        conn.commit()
        print("committed")
    conn.close()


if __name__ == "__main__":
    main()
