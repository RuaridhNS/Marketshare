#!/usr/bin/env python3
"""Give every regatta the country whose MARKET it belongs to.

regattas.country is read off the venue, and for a point-to-point race the venue
is the port it finishes in. Cowes to Cherbourg is therefore France, Cowes to
Alderney is Guernsey, and a site scoped to the United Kingdom loses eleven
races, 1,090 boats and 6,213 entries of a fleet that is 55% British.

The area already knew: those races are "Solent offshore". This writes the
area's home country onto the regatta as market_country, which is what the
dashboard scopes and filters on. regattas.country is left exactly as it is,
because it is not wrong - the venue really is in France - it just answers a
different question.

An area with no country in the ledger spans several for real, and its regattas
keep their own. A regatta with no area - no venue to place it - keeps its own
too.

    python scripts/apply_area_countries.py db/marketshare.db
    python scripts/apply_area_countries.py db/marketshare.db --apply
"""
import argparse, csv, datetime, pathlib, shutil, sqlite3

ROOT = pathlib.Path(__file__).resolve().parents[1]
LEDGER = ROOT / "data" / "area_countries.csv"


def load():
    if not LEDGER.exists():
        raise SystemExit(f"no ledger at {LEDGER}")
    out = {}
    with LEDGER.open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(l for l in fh if not l.startswith("#")):
            area = (row.get("Area") or "").strip()
            country = (row.get("Country") or "").strip()
            if area:
                out[area] = country or None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    areas = load()
    named = sum(1 for v in areas.values() if v)
    print(f"{len(areas)} area(s) in the ledger, {named} with a home country, "
          f"{len(areas) - named} that span more than one")

    dbp = pathlib.Path(a.db)
    con = sqlite3.connect(dbp)
    cur = con.cursor()
    cols = [r[1] for r in cur.execute("pragma table_info(regattas)")]
    if "market_country" not in cols:
        if not a.apply:
            print("regattas.market_country does not exist yet; --apply creates it")
        else:
            cur.execute("alter table regattas add column market_country TEXT")
            con.commit()
            print("added regattas.market_country")
            cols.append("market_country")

    sel = ("SELECT id, name, area, country, market_country FROM regattas"
           if "market_country" in cols else
           "SELECT id, name, area, country, NULL FROM regattas")
    rows = cur.execute(sel).fetchall()

    unknown, changes, moved = {}, [], []
    for rid, name, area, country, cur_mkt in rows:
        # The area's country when it has one, otherwise the regatta's own.
        want = areas.get(area) if area else None
        if area and area not in areas:
            unknown.setdefault(area, 0)
            unknown[area] += 1
        want = want or country
        if (cur_mkt or None) != (want or None):
            changes.append((want, rid))
        # The interesting ones: the market is NOT where the venue is.
        if want and country and want != country:
            moved.append((name, area, country, want))

    print(f"\nregattas to write: {len(changes)}")
    if unknown:
        print(f"{len(unknown)} area(s) on a regatta but NOT in the ledger - their "
              f"regattas keep their own country:")
        for k, n in list(unknown.items())[:8]:
            print(f"   {k!r}: {n} regatta(s)")

    print(f"\n{len(moved)} regatta(s) whose MARKET is not where the venue is:")
    for name, area, got, want in moved:
        print(f"   {got} -> {want}   {str(area):22} {name[:44]}")

    if not a.apply:
        print("\nDRY RUN - pass --apply")
        return

    bak = dbp.with_suffix(f".db.bak-market-{datetime.datetime.now():%H%M%S}")
    shutil.copy2(dbp, bak)
    print("\nbackup ->", bak.name)
    cur.executemany("UPDATE regattas SET market_country = ? WHERE id = ?", changes)
    con.commit()
    print(f"committed {len(changes)} market_country value(s)")


if __name__ == "__main__":
    main()
