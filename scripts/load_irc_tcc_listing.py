#!/usr/bin/env python3
"""Load IRC certificates from the Rating Office's online TCC listing.

This is the real thing, and it outranks everything else we hold. A TCC taken
off a results page is whatever the organiser typed; a certificate is what the
Rating Office issued. refresh_tcc.py derives a rating from the last result a
boat raced under precisely because this was missing - now that it is here,
this runs after it and overrules it.

Input is one or more ClubListing CSVs as published, named by year:

    Boat Name,Sail No,Cert No,Issue Date,Cert Year,TCC,Endorsed,Secondary,
    Non Spi TCC,Crew,DLR,LH,Beam,Draft,...

Pass a directory and every ClubListing*.csv in it is read, so a season's
worth of listings loads in one go and a new one is added by dropping the file
in. Pass the bundled extract (data/irc_tcc_certificates.csv) and it reads that
instead - same columns, fewer of them, which is what makes the project
self-contained on a machine without the shared drive.

Certificates land in boat_irc_certs, one row per (boat, cert year, cert no),
because a TCC is a fact about a boat IN A SEASON rather than about the boat.
boats.tcc then gets the most recent one.

Boats are matched on sail number alone, normalised to letters and digits.
Matching on name as well would be worse than the gap it closes: boat names
repeat constantly - this database holds two RIOTs - and a wrong certificate is
a wrong rating stated with the authority of the Rating Office.

    python scripts/load_irc_tcc_listing.py db/marketshare.db data/irc_tcc_certificates.csv
    python scripts/load_irc_tcc_listing.py db/marketshare.db "<dir of ClubListings>" --apply
"""
import argparse, csv, datetime, pathlib, re, shutil, sqlite3, sys

SCHEMA = """
CREATE TABLE IF NOT EXISTS boat_irc_certs (
    id            INTEGER PRIMARY KEY,
    boat_id       INTEGER NOT NULL REFERENCES boats(id) ON DELETE CASCADE,
    cert_year     INTEGER,
    cert_no       TEXT,
    tcc           REAL NOT NULL,
    non_spi_tcc   REAL,
    endorsed      TEXT,
    secondary     TEXT,          -- 'SEC' / 'Shorthanded': a second configuration, not the boat
    issue_date    TEXT,          -- ISO, or the published string if unparseable
    boat_name     TEXT,          -- the name ON the certificate, which may differ
    source        TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_birc_unique
    ON boat_irc_certs(boat_id, cert_year, cert_no);
CREATE INDEX IF NOT EXISTS idx_birc_boat ON boat_irc_certs(boat_id);
"""


def key(s):
    """Sail number reduced to letters, digits, and the hyphens that are part of
    the number.

    'GBR 8809R' -> 'GBR8809R' and 'ITA-15202' -> 'ITA15202': a hyphen after the
    country prefix is punctuation and two sources spell it differently.

    'GBR24-2' stays 'GBR24-2'. A hyphen BETWEEN TWO DIGITS is structural - it
    is the metre and class rule's own notation, GBR17-6M being boat 17 in the
    6-Metre class - and closing it up invents a different number. Stripping it
    is what gave TEAM MAVERICK SSR, a Diam 24 trimaran on GBR24-2, the four
    certificates belonging to SCENARIO ENCORE on GBR242, and made two
    unrelated boats look like one hull renamed."""
    s = (s or "").upper()
    s = re.sub(r"(?<=\d)-(?=\d)", "\x00", s)      # hold the structural hyphen
    s = re.sub(r"[^A-Z0-9\x00]", "", s)
    return s.replace("\x00", "-")


# A national sail number: a country prefix, digits, an optional class letter.
# Bare numbers are refused as a matching key. 224 of our boats share a bare
# number with a certificate - '0', '1', '007', '102' - and a bare number is not
# an identity: two of ours sit on '45', BELLA MENTE and GOSLING. The cost of
# being wrong here is a wrong rating carrying the Rating Office's authority,
# which is worse than no rating at all.
PLAUSIBLE = re.compile(r"^[A-Z]{2,4}\d+(-\d+)?[A-Z]{0,2}$")


def iso(d):
    """'16/07/2026' -> '2026-07-16'. Anything else comes back unchanged."""
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", (d or "").strip())
    return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}" if m else (d or "").strip()


def read_listing(path):
    """Yield certificate dicts from one ClubListing CSV."""
    with path.open(newline="", encoding="utf-8-sig") as fh:
        # the bundled extract carries a comment header saying where it came from
        lines = [l for l in fh if not l.startswith("#")]
    for row in csv.DictReader(lines):
        sail = key(row.get("Sail No"))
        if not sail:
            continue
        try:
            tcc = float((row.get("TCC") or "").strip())
        except ValueError:
            continue
        if not tcc:
            continue
        try:
            non_spi = float((row.get("Non Spi TCC") or "").strip())
        except ValueError:
            non_spi = None
        year = (row.get("Cert Year") or "").strip()
        yield {
            "sail": sail,
            "name": (row.get("Boat Name") or "").strip(),
            "cert_no": (row.get("Cert No") or "").strip(),
            "year": int(year) if year.isdigit() else None,
            "tcc": tcc,
            "non_spi": non_spi,
            "endorsed": (row.get("Endorsed") or "").strip() or None,
            # two spellings of one idea: the listing called this column
            # 'Short Handed' up to 2021 and 'Secondary' after it
            "secondary": ((row.get("Secondary") or row.get("Short Handed") or "").strip()
                          or None),
            "issued": iso(row.get("Issue Date")),
            "source": path.name,
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("path", help="a ClubListing CSV, a directory of them, or the bundled extract")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    src = pathlib.Path(a.path)
    if src.is_dir():
        files = sorted(p for p in src.glob("*.csv") if p.name.lower().startswith("clublisting"))
    else:
        files = [src]
    if not files:
        raise SystemExit(f"no ClubListing CSVs at {src}")

    certs = []
    for f in files:
        got = list(read_listing(f))
        print(f"  {f.name}: {len(got)} certificate(s)")
        certs.extend(got)
    by_sail = {}
    for c in certs:
        by_sail.setdefault(c["sail"], []).append(c)
    print(f"\n{len(certs)} certificate row(s) over {len(by_sail)} boat(s), "
          f"from {len(files)} listing(s)")

    dbp = pathlib.Path(a.db)
    if a.apply:
        bak = dbp.with_suffix(f".db.bak-certs-{datetime.datetime.now():%H%M%S}")
        shutil.copy2(dbp, bak)
        print("backup ->", bak.name)
    con = sqlite3.connect(dbp)
    cur = con.cursor()
    if a.apply:
        cur.executescript(SCHEMA)

    ours = {}
    for bid, sail, name, tcc in cur.execute(
            "SELECT id, sail_no, boat_name, tcc FROM boats WHERE sail_no IS NOT NULL"):
        ours.setdefault(key(sail), []).append((bid, name, tcc))

    refused = sorted(k for k in ours if k in by_sail and not PLAUSIBLE.match(k))
    matched = {k: v for k, v in ours.items() if k in by_sail and PLAUSIBLE.match(k)}
    print(f"matched {len(matched)} of our {len(ours)} sail number(s) to a certificate")
    if refused:
        print(f"  refused {len(refused)} match(es) on a sail number that is not an identity "
              f"- a bare number with no country prefix: " + ", ".join(refused[:12]) + " ...")
    shared = [k for k, v in matched.items() if len(v) > 1]
    if shared:
        print(f"  {len(shared)} sail number(s) are on more than one of our boat records - "
              f"the certificate is written to each, which is right if they are duplicates "
              f"of one boat and is worth a look if they are not:")
        for k in shared[:6]:
            print(f"     {k}: " + ", ".join(str(b[1]) for b in ours[k]))

    # the newest certificate per boat, which is what boats.tcc should say
    rows, latest = [], {}
    for k, boats in matched.items():
        for c in by_sail[k]:
            for bid, _, _ in boats:
                rows.append((bid, c["year"], c["cert_no"], c["tcc"], c["non_spi"],
                             c["endorsed"], c["secondary"], c["issued"], c["name"],
                             c["source"]))
                cur_best = latest.get(bid)
                # A primary certificate outranks a secondary one even when the
                # secondary is newer: a secondary is a second CONFIGURATION of
                # the same boat - short-handed, or a different sail plan - and
                # it is not what the boat rates. Both are stored; only one is
                # the headline.
                stamp = (0 if c["secondary"] else 1, c["year"] or 0, c["issued"] or "")
                if not cur_best or stamp > cur_best[0]:
                    latest[bid] = (stamp, c["tcc"])

    old_tcc = {bid: tcc for v in ours.values() for bid, _, tcc in v}
    changed = [(bid, t) for bid, (_, t) in latest.items()
               if old_tcc[bid] is None or abs(old_tcc[bid] - t) > 0.0005]
    filled = sum(1 for bid, _ in changed if old_tcc[bid] is None)
    print(f"\n{len(rows)} certificate row(s) to store; {len(latest)} boat(s) carry one")
    print(f"boats.tcc to change: {len(changed)}  ({filled} of them currently have none)")

    if not a.apply:
        print("\nDRY RUN - pass --apply")
        return

    cur.executemany(
        "INSERT INTO boat_irc_certs (boat_id, cert_year, cert_no, tcc, non_spi_tcc, endorsed, "
        "secondary, issue_date, boat_name, source) VALUES (?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(boat_id, cert_year, cert_no) DO UPDATE SET "
        "tcc=excluded.tcc, non_spi_tcc=excluded.non_spi_tcc, endorsed=excluded.endorsed, "
        "secondary=excluded.secondary, issue_date=excluded.issue_date, "
        "boat_name=excluded.boat_name, source=excluded.source",
        rows)
    cur.executemany("UPDATE boats SET tcc = ? WHERE id = ?", [(t, b) for b, t in changed])
    con.commit()
    print(f"\nstored {len(rows)} certificate(s); set {len(changed)} boat rating(s) "
          f"from the certificate rather than from a results page")


if __name__ == "__main__":
    main()
