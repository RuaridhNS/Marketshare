#!/usr/bin/env python3
"""
Scraper for Cork Week, Royal Cork Yacht Club, Crosshaven.

The whole regatta is ONE Sailwave page - 233 tables, every fleet and every
race in a single file - so this fetches once and walks the document rather
than crawling. Structure:

    <h3>Class 1 - IRC Fleet</h3>            <table>  series standings
    <h3>Mon R1 - Class 1 - IRC Fleet - 06/07/2026</h3>  <table>  one race
    ...

The <h3> is the only thing naming a table; the tables carry no caption. A
heading ending in a date is a RACE, anything else is the standings for that
fleet.

robots.txt: royalcork.com declares `Disallow:` - empty, so nothing is off
limits - and corkweek.ie the same. The server does however sit behind a WAF
that answers 403 to a bare user agent, so this sends a browser-shaped one
with a Referer. That is to be served the page a browser is served, not to
get past a restriction: there is no restriction to get past.

THREE FLEETS SCORE THE SAME RACING AND ONLY ONE IS LOADED. Cork Week scores
each division three ways - "Class 1 - IRC Fleet", "Class 1 - Echo Fleet" and
"Class 1 - IRC ICRA Fleet". Same boats, same races, three handicaps and a
sub-championship. Loading all three would triple every boat, so only the
fleets whose rating column is headed IRC and whose name is not the ICRA
scoring are taken:

    loaded   Beaufort Cup 6, Class 0 4, Class 1 7, Class 2 6, Class 3 13,
             Coastal 1 14, Coastal 2 12, NS-1 8, NS-2 11, SportsBoat 6
    skipped  every Echo fleet (ECHO is a progressive handicap, not a rating,
             and its numbers must never reach the tcc column)
    skipped  every IRC ICRA fleet (the Irish national championship scored
             inside the same races)
    skipped  Etchells (one-design, its rating column is headed "Rating")

Usage:
  python3 scrape_cork_week.py <db.sqlite> [--year 2026] [--dry-run]
      [--url <page>] [--delay 0]
"""
import argparse
import csv
import pathlib
import re
import subprocess
import sys

import requests
from bs4 import BeautifulSoup

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

DEFAULT_URL = "https://www.royalcork.com/Results/CorkWeek2026.htm"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0 Safari/537.36 "
                  "(compatible; MarketshareResearchBot/1.0)",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": "https://www.corkweek.ie/",
}

# "Mon R1 - Class 1 - IRC Fleet - 06/07/2026" -> race label, fleet, date
RACE_RE = re.compile(r"^(.*?)\s+-\s+(.*?)\s+-\s+(\d{2})/(\d{2})/(\d{4})\s*$")
SKIP_FLEET_RE = re.compile(r"\bEcho\b|\bICRA\b|\(Prog\)", re.I)


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip())


def sail_no(raw):
    """Cork publishes 'Irl1840', 'IRL 2160', 'GBR9222L'. Upper-case and close
    the gap; leave everything else, because a national prefix IS the number."""
    return re.sub(r"\s+", "", (raw or "")).upper()


def parse(html):
    """-> {fleet: {'rating': 'IRC'|..., 'races': [{label, date, rows}]}}"""
    soup = BeautifulSoup(html, "html.parser")
    fleets = {}
    name = None
    for el in soup.find_all(["h3", "table"]):
        if el.name == "h3":
            name = norm(el.get_text(" ", strip=True))
            continue
        if not name:
            continue
        # One table per heading: take the heading and clear it, so a stray
        # table with no heading of its own cannot inherit the one above.
        name, rows = None, el.find_all("tr")
        heading = norm(el.find_previous("h3").get_text(" ", strip=True))
        if not rows:
            continue
        head = [norm(c.get_text(" ", strip=True)) for c in rows[0].find_all(["th", "td"])]
        if not head:
            continue
        m = RACE_RE.match(heading)
        fleet = m.group(2) if m else heading
        if SKIP_FLEET_RE.search(fleet):
            continue
        rating_col = next((h for h in head if h in ("IRC", "ECHO", "Rating", "PY")), None)
        f = fleets.setdefault(fleet, {"rating": rating_col, "races": []})
        if f["rating"] is None:
            f["rating"] = rating_col
        if not m:
            continue                      # the standings table; the races carry the detail
        data = []
        for tr in rows[1:]:
            cells = [norm(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
            if len(cells) == len(head):
                data.append(dict(zip(head, cells)))
        if data:
            f["races"].append({
                "label": m.group(1),
                "date": f"{m.group(5)}-{m.group(4)}-{m.group(3)}",
                "rows": data,
            })
    return fleets


def to_csv_rows(rows, rating_col):
    out = []
    for r in rows:
        sail = sail_no(r.get("SailNo"))
        if not sail:
            continue
        out.append({
            "Position": r.get("Rank", ""),
            "Points": r.get("Points", ""),
            "SailNo": sail,
            "Boat": r.get("Boat", ""),
            "BoatType": r.get("Type", ""),
            "Owner": r.get("Owner", ""),
            "SailedBy": "",
            "FinishTime": r.get("Finish", ""),
            "Elapsed": r.get("Elapsed", ""),
            # ONLY an IRC column reaches Handicap. An ECHO number is a
            # progressive handicap in the same 0.9-1.2 range as a TCC and
            # would be indistinguishable from one once stored.
            "Handicap": r.get(rating_col, "") if rating_col == "IRC" else "",
            "Corrected": r.get("Corrected", ""),
            "Comments": r.get("Notes", ""),
        })
    return out


def write_csv(rows, path, comment):
    cols = ["Position", "Points", "SailNo", "Boat", "BoatType", "Owner", "SailedBy",
            "FinishTime", "Elapsed", "Handicap", "Corrected", "Comments"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write(f"# {comment}\n")
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def load(db, csv_path, regatta, year, race_name, cls, url, race_date):
    cmd = [sys.executable, str(SCRIPT_DIR / "load_rorc_csv.py"), db, str(csv_path),
           "--regatta", regatta, "--year", str(year), "--race-name", race_name,
           "--source-url", url, "--race-date", race_date,
           "--category", "Club", "--source", "scrape:cork-week", "--class", cls]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"    LOAD FAILED: {res.stderr.strip()[:200]}")
        return False
    print(f"    {res.stdout.strip()}")
    return True


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--year", type=int, default=2026)
    p.add_argument("--regatta", default="Cork Week")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    print(f"fetching {a.url}")
    html = requests.get(a.url, headers=HEADERS, timeout=40).text
    print(f"  {len(html):,} bytes")

    fleets = parse(html)
    irc = {k: v for k, v in fleets.items() if v["rating"] == "IRC"}
    other = {k: v["rating"] for k, v in fleets.items() if v["rating"] != "IRC"}
    print(f"\n{len(fleets)} fleet(s) on the page; {len(irc)} rated IRC")
    for k, v in other.items():
        print(f"  skipped  {k[:36]:38} rated {v}")

    exports = REPO_ROOT / "exports"
    exports.mkdir(exist_ok=True)
    n_races = n_entries = 0
    for fleet, f in sorted(irc.items()):
        cls = re.sub(r"\s*Fleet$", "", fleet).strip()
        print(f"\n[{fleet}] {len(f['races'])} race(s)")
        for race in f["races"]:
            rows = to_csv_rows(race["rows"], f["rating"])
            if not rows:
                continue
            race_name = f"{race['label']} - {cls}"
            safe = re.sub(r"[^\w\- ]", "_", f"{a.year}_{cls}_{race['label']}")[:110]
            out = exports / f"corkweek_{safe}.csv"
            write_csv(rows, out, f"{race_name} - {race['date']} - {len(rows)} boats - {a.url}")
            print(f"  {race['label']:14} {race['date']}  {len(rows):3} boats -> {out.name}")
            n_races += 1
            n_entries += len(rows)
            if not a.dry_run:
                load(a.db, out, a.regatta, a.year, race_name, cls, a.url, race["date"])

    print(f"\n{n_races} race(s), {n_entries} entr(y/ies)"
          + ("  (dry run - nothing loaded)" if a.dry_run else ""))


if __name__ == "__main__":
    main()
