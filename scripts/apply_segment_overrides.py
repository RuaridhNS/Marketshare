#!/usr/bin/env python3
"""Set regattas' market segments from data/segment_overrides.csv.

The importer guesses a segment from the regatta name, and a guess is wrong
often enough to need a way to correct it that survives the next refresh. That
is what this ledger is: the file is the truth, re-applied every time, so a
correction made once stays made.

    python scripts/apply_segment_overrides.py db/marketshare.db
    python scripts/apply_segment_overrides.py db/marketshare.db --apply
"""
import argparse, csv, datetime, pathlib, re, shutil, sqlite3

LEDGER = pathlib.Path(__file__).resolve().parents[1] / "data" / "segment_overrides.csv"
SEGMENTS = {"Grand Prix", "Premier Race", "Race", "One-Design", "Classic",
            "Cruise", "Premier Cruise"}


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
            pat, seg = row[0].strip(), row[1].strip()
            if seg not in SEGMENTS:
                print(f"  skipping {pat!r}: {seg!r} is not a segment")
                continue
            try:
                out.append((re.compile(pat, re.I), seg, pat))
            except re.error as ex:
                print(f"  skipping bad pattern {pat!r}: {ex}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    rules = load()
    print(f"rules: {len(rules)}")
    dbp = pathlib.Path(a.db)
    if a.apply:
        bak = dbp.with_suffix(f".db.bak-seg-{datetime.datetime.now():%H%M%S}")
        shutil.copy2(dbp, bak)
        print("backup ->", bak.name)
    con = sqlite3.connect(dbp)
    cur = con.cursor()

    rows = list(cur.execute("SELECT id, name, segment FROM regattas ORDER BY name"))
    changes, unused = [], {p for _, _, p in rules}
    for rid, name, seg in rows:
        hit = next((r for r in rules if r[0].search(name or "")), None)
        if not hit:
            continue
        unused.discard(hit[2])
        if seg != hit[1]:
            changes.append((rid, name, seg, hit[1]))

    print(f"\nregattas to change: {len(changes)}")
    for rid, name, was, now in changes:
        print(f"   {str(was or '(none)'):<15} -> {now:<12} {name[:48]}")
    if unused:
        print(f"\npatterns that matched nothing ({len(unused)}):")
        for p in sorted(unused):
            print(f"   {p}")

    if not a.apply:
        print("\nDRY RUN - pass --apply")
        return
    cur.executemany("UPDATE regattas SET segment=? WHERE id=?",
                    [(now, rid) for rid, _, _, now in changes])
    con.commit()
    print(f"\ncommitted {len(changes)} segment change(s)")


if __name__ == "__main__":
    main()
