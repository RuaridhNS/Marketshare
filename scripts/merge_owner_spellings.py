#!/usr/bin/env python3
"""
Fold owner records that are one person spelled several ways.

10,417 owner records, and 545 of them are 268 people. "DAVID  FRANKS" with
two spaces and "DAVID FRANKS" with one. "ANTHONY O'LEARY" and "ANTHONY O
LEARY". "CHRIS RUST, JAMES VINEER" and "CHRIS RUST & JAMES VINEER". "PAUL AND
JULIA AISHER" and "PAUL & JULIA AISHER".

This costs more than a tidy count. Ownership is INFERRED from whoever a
results page printed, season by season, so two spellings of one person read
as two owners and backfill_owners.py writes a change of ownership between
them - and then a change back when the next season reverts to the first
spelling. 599 boats on the dashboard show an owner who appears, goes away and
returns; a third of those are this and nothing more.

WHAT IS AND IS NOT JUDGED HERE. Only ORTHOGRAPHY. Two records are the same
person when their names agree after case, spacing, punctuation and the choice
between "and" and "&" are taken out. Nothing about who owns what is decided:
"NICK EDMONDS" and "NICK & JACQUETTA EDMONDS" are left as two records,
because one of them names a second person and this script has no business
deciding that they are the same ownership. That question belongs to
data/owner_corrections.csv and apply_owner_timeline.py, where a person answers
it one boat at a time.

WHICH SPELLING SURVIVES. The one carrying the most ownership history, then
the most entries, then - as a tie-break that only ever decides between two
equally-used spellings - the one that is not double-spaced and does not have
a space where an apostrophe should be. A charter operator flag on either
record is carried to the survivor; losing it would turn a charterer into an
owner.

Idempotent: once folded, the losing spelling no longer exists and the group
is a group of one.

Usage:
  python3 merge_owner_spellings.py <db.sqlite> [--apply]
"""
import argparse
import collections
import datetime
import pathlib
import re
import shutil
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
# ONE definition of what makes two spellings the same person, and it lives in
# build_db beside get_or_create_owner, because that is where every loader
# resolves a name. A second copy here would drift, and then this script and
# the loaders would disagree about who is who - which is how the folded
# spellings came back the first time this was run.
from build_db import owner_key as key

# Tables that point at an owner, and the column that does it.
REFS = [("boats", "current_owner_id"),
        ("race_entries", "owner_id"),
        ("boat_owner_history", "owner_id")]


def say(*parts):
    """print(), but this console is cp1252 and a French owner is not. Without
    it the run dies partway through its own report on the first accent, which
    is worse than useless in a script meant to run unattended."""
    line = " ".join(str(p) for p in parts)
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(line.encode(enc, "backslashreplace").decode(enc, "replace"))


def tidiness(name):
    """Lower is tidier. Only ever used to break a tie between two spellings
    that are equally well used."""
    n = name or ""
    return (len(re.findall(r"\s{2,}", n))            # double spaces
            + len(re.findall(r"\b[A-Z]\s(?=[A-Z])", n))  # 'O LEARY' for "O'LEARY"
            + (1 if "," in n else 0)                 # a comma where '&' belongs
            # Prefer the accented spelling: FREDERIC and FREDERIC-with-acutes
            # are one man, and the accented one is the one that is right.
            - (1 if any(ord(ch) > 127 for ch in n) else 0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    dbp = pathlib.Path(a.db)
    con = sqlite3.connect(dbp)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # Counted with three grouped passes rather than three correlated
    # subqueries per owner. The subquery form is 10,417 scans of a
    # 150,000-row table and does not finish in five minutes.
    def tally(sql):
        return {r[0]: r[1] for r in cur.execute(sql) if r[0] is not None}

    nh = tally("SELECT owner_id, COUNT(*) FROM boat_owner_history GROUP BY owner_id")
    ne = tally("SELECT owner_id, COUNT(*) FROM race_entries GROUP BY owner_id")
    nb = tally("SELECT current_owner_id, COUNT(*) FROM boats GROUP BY current_owner_id")
    rows = [{"id": r["id"], "name": r["name"], "charter": r["is_charter_operator"],
             "nh": nh.get(r["id"], 0), "ne": ne.get(r["id"], 0), "nb": nb.get(r["id"], 0)}
            for r in cur.execute(
                "SELECT id, name, is_charter_operator FROM owners")]

    groups = collections.defaultdict(list)
    for r in rows:
        k = key(r["name"])
        if k:
            groups[k].append(r)
    dupes = {k: v for k, v in groups.items() if len(v) > 1}

    print(f"{len(rows):,} owner record(s); {len(dupes)} name(s) spelled more than one way, "
          f"covering {sum(len(v) for v in dupes.values())} records")
    if not dupes:
        return

    plan = []
    for k, v in dupes.items():
        v = sorted(v, key=lambda r: (-r["nh"], -r["ne"], tidiness(r["name"]), r["id"]))
        keep, fold = v[0], v[1:]
        plan.append((keep, fold))

    for keep, fold in sorted(plan, key=lambda p: -sum(f["nb"] for f in p[1]))[:14]:
        say(f"  keep {keep['name']!r:46} <- "
            + ", ".join(f"{f['name']!r}" for f in fold))
    if len(plan) > 14:
        print(f"  ... and {len(plan) - 14} more group(s)")

    moved = sum(f["nh"] + f["ne"] + f["nb"] for _, fold in plan for f in fold)
    print(f"\n{sum(len(f) for _, f in plan)} record(s) would be folded away, "
          f"moving {moved:,} reference(s)")

    if not a.apply:
        print("DRY RUN - pass --apply")
        return

    bak = dbp.with_suffix(f".db.bak-owners-{datetime.datetime.now():%H%M%S}")
    shutil.copy2(dbp, bak)
    say("backup ->", bak.name)

    folded = 0
    for keep, fold in plan:
        for f in fold:
            for table, col in REFS:
                cur.execute(f"UPDATE {table} SET {col} = ? WHERE {col} = ?",
                            (keep["id"], f["id"]))
            # A charter flag on the loser has to survive the fold, or the
            # charterer silently becomes eligible to be somebody's owner.
            if f["charter"] and not keep["charter"]:
                cur.execute("UPDATE owners SET is_charter_operator = 1 WHERE id = ?",
                            (keep["id"],))
            cur.execute("DELETE FROM owners WHERE id = ?", (f["id"],))
            folded += 1

    # The fold can leave a boat with the same owner twice in a row, which is
    # not a change of ownership and should not read as one.
    dropped = 0
    for bid, in cur.execute("SELECT DISTINCT boat_id FROM boat_owner_history").fetchall():
        segs = cur.execute(
            "SELECT id, owner_id, effective_from FROM boat_owner_history "
            "WHERE boat_id = ? ORDER BY effective_from, id", (bid,)).fetchall()
        prev = None
        for s in segs:
            if prev is not None and s["owner_id"] == prev:
                cur.execute("DELETE FROM boat_owner_history WHERE id = ?", (s["id"],))
                dropped += 1
            else:
                prev = s["owner_id"]

    con.commit()
    print(f"folded {folded} owner record(s); "
          f"dropped {dropped} ownership segment(s) that repeated the one before")


if __name__ == "__main__":
    main()
