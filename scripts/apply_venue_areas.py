#!/usr/bin/env python3
"""Give every regatta an area, from data/venue_areas.csv.

The database already knows WHERE a regatta is - data/venues.csv puts a venue
and a coordinate on it - but ninety-five venues is too fine to ask a question
of. "Show me the Solent" and "show me the South Coast" need a layer above the
venue, and that is what this is.

It is NOT regattas.region. That column records whose race it is, not where:
the Fastnet, Cowes-Dinard, the Middle Sea Race and the Caribbean 600 all sit
under region='Solent' because RORC runs them from Cowes. Useful for other
questions, useless for this one.

Matched exactly on the venue string, because venues.csv is the only thing that
writes it and the two files should disagree loudly rather than quietly. A
regatta with no venue, or a venue with no area, gets NULL and groups under
"Area not set" in the dashboard.

    python scripts/apply_venue_areas.py db/marketshare.db
    python scripts/apply_venue_areas.py db/marketshare.db --apply
"""
import argparse, csv, datetime, pathlib, shutil, sqlite3

LEDGER = pathlib.Path(__file__).resolve().parents[1] / "data" / "venue_areas.csv"


def load():
    if not LEDGER.exists():
        raise SystemExit(f"no ledger at {LEDGER}")
    out = {}
    with LEDGER.open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(l for l in fh if not l.startswith("#")):
            v = (row.get("Venue") or "").strip()
            a = (row.get("Area") or "").strip()
            if v:
                out[v] = a or None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    areas = load()
    print(f"{len(areas)} venue(s) in the ledger, "
          f"{sum(1 for v in areas.values() if v)} with an area")

    dbp = pathlib.Path(a.db)
    con = sqlite3.connect(dbp)
    cur = con.cursor()
    cols = [r[1] for r in cur.execute("pragma table_info(regattas)")]
    if "area" not in cols:
        if not a.apply:
            print("regattas.area does not exist yet; --apply creates it")
        else:
            cur.execute("alter table regattas add column area TEXT")
            con.commit()
            print("added regattas.area")
            cols.append("area")

    sel = ("SELECT id, name, venue, area FROM regattas" if "area" in cols
           else "SELECT id, name, venue, NULL FROM regattas")
    rows = cur.execute(sel).fetchall()

    changes, unknown = [], {}
    for rid, name, venue, cur_area in rows:
        want = areas.get((venue or "").strip()) if venue else None
        if venue and (venue or "").strip() and (venue or "").strip() not in areas:
            unknown.setdefault(venue, []).append(name)
        if (cur_area or None) != want:
            changes.append((rid, name, venue, cur_area, want))

    print(f"\nregattas to change: {len(changes)}")
    for _, name, venue, was, now in changes[:10]:
        print(f"   {str(was or '-'):22} -> {str(now or '(none)'):26} "
              f"{str(venue or '(no venue)')[:20]:22} {name[:30]}")
    if len(changes) > 10:
        print(f"   ... and {len(changes) - 10} more")
    if unknown:
        print(f"\n{len(unknown)} venue(s) are on a regatta but not in the ledger - "
              f"they will have no area:")
        for v, names in list(unknown.items())[:8]:
            print(f"   {v!r}: {len(names)} regatta(s), e.g. {names[0][:40]}")

    noven = sum(1 for _, _, v, _ in rows if not (v or "").strip())
    if noven:
        print(f"\n{noven} regatta(s) have no venue at all, so no area. "
              f"data/venues.csv is where that is fixed.")

    if not a.apply:
        print("\nDRY RUN - pass --apply")
        return

    bak = dbp.with_suffix(f".db.bak-area-{datetime.datetime.now():%H%M%S}")
    shutil.copy2(dbp, bak)
    print("backup ->", bak.name)
    cur.executemany("UPDATE regattas SET area = ? WHERE id = ?",
                    [(now, rid) for rid, _, _, _, now in changes])
    con.commit()
    print(f"\ncommitted {len(changes)} area change(s)")


if __name__ == "__main__":
    main()
