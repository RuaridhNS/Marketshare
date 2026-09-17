#!/usr/bin/env python3
"""Put every regatta on the map, from data/venues.csv.

The database has never held coordinates - a regatta knows its name and its
region, not where it is. This matches each regatta name against the venue
gazetteer and writes venue/lat/lon onto the regatta, so the globe has
something to plot.

Nothing is guessed: a regatta that matches no pattern is left unplaced and
reported, so the gap is visible rather than silently landing in the Atlantic.

    python scripts/geocode_regattas.py db/marketshare.db            # dry run
    python scripts/geocode_regattas.py db/marketshare.db --apply
"""
import argparse, csv, datetime, pathlib, re, shutil, sqlite3

VENUES = pathlib.Path(__file__).resolve().parents[1] / "data" / "venues.csv"


def load_venues():
    """-> [(compiled pattern, venue, country, lat, lon)] in file order."""
    out = []
    with VENUES.open(encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            row = next(csv.reader([line]))
            if len(row) < 5 or row[0].strip().lower() == "pattern":
                continue
            pat, venue, country, lat, lon = (c.strip() for c in row[:5])
            try:
                out.append((re.compile(pat, re.I), venue, country,
                            float(lat), float(lon)))
            except (re.error, ValueError) as ex:
                print(f"  skipping bad venue row {pat!r}: {ex}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    venues = load_venues()
    print(f"venue patterns: {len(venues)}")

    dbp = pathlib.Path(a.db)
    if a.apply:
        bak = dbp.with_suffix(f".db.bak-geo-{datetime.datetime.now():%H%M%S}")
        shutil.copy2(dbp, bak)
        print("backup ->", bak.name)
    con = sqlite3.connect(dbp)
    cur = con.cursor()

    cols = [r[1] for r in cur.execute("pragma table_info(regattas)")]
    for c, t in (("venue", "TEXT"), ("lat", "REAL"), ("lon", "REAL")):
        if c not in cols:
            cur.execute(f"alter table regattas add column {c} {t}")
            print(f"added regattas.{c}")

    rows = list(cur.execute("""
        select r.id, r.name, count(distinct re.boat_id) boats
          from regattas r
          left join events e on e.regatta_id = r.id
          left join races ra on ra.event_id = e.id
          left join race_entries re on re.race_id = ra.id
         group by r.id order by boats desc"""))

    # Region is already recorded and is trustworthy: every regatta the original
    # Solent scrapers built carries region='Solent'. A club series called
    # "Summer Series" or a JOG race named after its sponsor will never match a
    # venue pattern, but we know exactly where it sailed.
    region = dict(cur.execute("select id, region from regattas"))
    FALLBACK = {"Solent": ("Solent", "GBR", 50.740, -1.310)}

    placed, unplaced, updates, by_region = [], [], [], 0
    for rid, name, boats in rows:
        hit = next((v for v in venues if v[0].search(name or "")), None)
        if hit:
            _, venue, country, lat, lon = hit
        elif region.get(rid) in FALLBACK:
            venue, country, lat, lon = FALLBACK[region[rid]]
            by_region += 1
        else:
            unplaced.append((name, boats))
            continue
        updates.append((venue, country, lat, lon, rid))
        placed.append((name, venue, boats))

    pb = sum(b for _, _, b in placed)
    ub = sum(b for _, b in unplaced)
    print(f"\nregattas placed   : {len(placed):>4}   boat-links {pb:>7,}")
    print(f"regattas unplaced : {len(unplaced):>4}   boat-links {ub:>7,}")
    print(f"   of those, placed by region rather than name: {by_region}")
    print(f"coverage by fleet : {100*pb/max(pb+ub,1):.1f}%")

    if unplaced:
        print(f"\n-- unplaced, largest first (add a pattern to data/venues.csv) --")
        for name, boats in sorted(unplaced, key=lambda x: -x[1])[:40]:
            print(f"   {boats:>5}  {name[:62]}")

    if not a.apply:
        print("\nDRY RUN - nothing written (pass --apply)")
        return
    cur.executemany("update regattas set venue=?, country=coalesce(country,?),"
                    " lat=?, lon=? where id=?", updates)
    con.commit()
    print(f"\ncommitted {len(updates)} regatta placements")


if __name__ == "__main__":
    main()
