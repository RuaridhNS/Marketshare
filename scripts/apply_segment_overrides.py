#!/usr/bin/env python3
"""Set regattas' market segments from data/segment_overrides.csv.

The importer guesses a segment from the regatta name, and a guess is wrong
often enough to need a way to correct it that survives the next refresh. That
is what this ledger is: the file is the truth, re-applied every time, so a
correction made once stays made.

A regatta may carry more than one segment, because a regatta is not a fleet:
Cowes Week runs IRC classes, one-design classes and a classics division under
one entry. Write them separated by a pipe, most important first:

    ^Cowes Week$,Race|One-Design|Classic,"Three fleets under one entry."

THE FIRST ONE COUNTS. It lands in regattas.segment, which is what the Market
tab, the globe and the segment totals group on, so a regatta's boats are
counted once and the segment totals still add up to the fleet. The rest land
in regattas.segments alongside it, and the dashboard lists the regatta under
those headings too, marked as counted elsewhere. Double-counting the same
boats into two segments would make the segments sum to more than the fleet,
which is a worse lie than a regatta appearing under one heading.

    python scripts/apply_segment_overrides.py db/marketshare.db
    python scripts/apply_segment_overrides.py db/marketshare.db --apply
"""
import argparse, csv, datetime, pathlib, re, shutil, sqlite3

LEDGER = pathlib.Path(__file__).resolve().parents[1] / "data" / "segment_overrides.csv"
SEGMENTS = {"Grand Prix", "Premier Race", "Race", "One-Design", "Classic",
            "Cruise", "Premier Cruise"}


def parse_segments(field):
    """'Race | One-Design' -> ['Race', 'One-Design'], order kept, dupes dropped.

    Returns (None, offender) if any part is not a segment, so a typo is
    reported rather than quietly writing something nothing else recognises."""
    out = []
    for part in str(field).split("|"):
        part = part.strip()
        if not part:
            continue
        if part not in SEGMENTS:
            return None, part
        if part not in out:
            out.append(part)
    return (out or None), None


def load():
    out = []
    if not LEDGER.exists():
        raise SystemExit(f"no ledger at {LEDGER}")
    with LEDGER.open(encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            row = next(csv.reader([line]))
            if len(row) < 2 or row[0].strip().lower() == "pattern":
                continue
            pat, field = row[0].strip(), row[1].strip()
            segs, bad = parse_segments(field)
            if segs is None:
                print(f"  skipping {pat!r}: {bad!r} is not a segment")
                continue
            try:
                out.append((re.compile(pat, re.I), segs, pat))
            except re.error as ex:
                print(f"  skipping bad pattern {pat!r}: {ex}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    rules = load()
    multi = sum(1 for _, segs, _ in rules if len(segs) > 1)
    print(f"rules: {len(rules)}" + (f" ({multi} carrying more than one segment)" if multi else ""))
    dbp = pathlib.Path(a.db)
    if a.apply:
        bak = dbp.with_suffix(f".db.bak-seg-{datetime.datetime.now():%H%M%S}")
        shutil.copy2(dbp, bak)
        print("backup ->", bak.name)
    con = sqlite3.connect(dbp)
    cur = con.cursor()

    # The full list lives BESIDE the primary rather than replacing it: every
    # query that groups by r.segment keeps working untouched, and keeps
    # counting each regatta exactly once.
    cols = [r[1] for r in cur.execute("pragma table_info(regattas)")]
    has_segments = "segments" in cols
    if not has_segments and a.apply:          # a dry run writes nothing, schema included
        cur.execute("alter table regattas add column segments TEXT")
        con.commit()
        has_segments = True
        print("added regattas.segments")

    rows = list(cur.execute(
        "SELECT id, name, segment, segments FROM regattas ORDER BY name" if has_segments
        else "SELECT id, name, segment, NULL FROM regattas ORDER BY name"))
    changes, unused = [], {p for _, _, p in rules}
    for rid, name, seg, segs in rows:
        hit = next((r for r in rules if r[0].search(name or "")), None)
        if not hit:
            continue
        unused.discard(hit[2])
        want = hit[1]
        want_joined = "|".join(want)
        # A regatta that has never been through this script has segments NULL;
        # read that as "whatever the primary says", so a single-segment row
        # already applied does not report as a change on every run.
        have = segs if segs is not None else (seg or "")
        if (seg or "") != want[0] or have != want_joined:
            changes.append((rid, name, seg, segs, want))

    print(f"\nregattas to change: {len(changes)}")
    for rid, name, was, was_all, now in changes:
        shown_was = was_all or was or "(none)"
        print(f"   {shown_was:<22} -> {'|'.join(now):<22} {name[:40]}")
    if unused:
        print(f"\npatterns that matched nothing ({len(unused)}):")
        for p in sorted(unused):
            print(f"   {p}")

    if not a.apply:
        print("\nDRY RUN - pass --apply")
        return
    cur.executemany("UPDATE regattas SET segment=?, segments=? WHERE id=?",
                    [(now[0], "|".join(now), rid) for rid, _, _, _, now in changes])
    con.commit()
    print(f"\ncommitted {len(changes)} segment change(s)")


if __name__ == "__main__":
    main()
