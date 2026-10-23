#!/usr/bin/env python3
"""
Scraper for Burnham Week, Burnham-on-Crouch - the major East Coast regatta.

Sailwave again, but laid out the other way round from Cork Week: one page per
(series, class) rather than one page for everything. The index at
/results-<year>/ links them all. Five series share the week - Bank Holiday
(BH), Mid Week (MW), Trophy Day (TD), Week's Points (WP) and the John
Torrance (JT) - and a boat can appear in several, so the series is carried
into the race name or they collide.

Page layout, headings rather than captions:
    <h3>Overall</h3>                        <table>  series standings
    <h3>R1 - 29/08/2026 at 11.40</h3>       <table>  one race

READ THIS BEFORE JUDGING THE NUMBERS IT ADDS. Burnham Week is not IRC. Every
handicap class - Class 1&2, Class 5, Class 6 - is scored on PORTSMOUTH
YARDSTICK, and the rest of the week is one-design: Squib, Royal Burnham OD,
Royal Corinthian OD, East Coast OD, Dragon, RS Elite, 707, and the dinghy
fleets. Checked across all 49 result pages of 2026: 139 entries under PY,
177 with no rating system at all, and not one under IRC.

So nothing here reaches the dashboard, which is IRC-scoped by design. It is
loaded anyway because the boats are real East Coast keelboats with real
owners - an X-37, a Sun Fast 3200, a J/105, an Elan 31 - and several of them
will hold IRC certificates and race IRC elsewhere. The records are worth
having even while the entries are filtered out. If the export is ever widened
past IRC, this is already here.

TWO COLUMNS THAT ARE NOT WHAT THEY SAY:
  * "Class" is the BOAT TYPE ("Elan 31", "Star flash MOD keel"). The racing
    class is in "Fleet". Same quirk as the Warsash Sailwave pages.
  * "YTC" is a Portsmouth number - 952, 963 - and NOT a rating. It is
    deliberately not written to Handicap: a TCC column holding 963 is exactly
    the fault export_dashboard_data.py guards against, and it would qualify a
    PY fleet as IRC.

Helm is recorded as SailedBy, never as Owner, for the same reason as Warsash:
the helm is usually but not always the owner, and inventing owner records
from helm names pollutes the owners table.

robots.txt (burnhamweek.com): disallows only /wp-admin/. Results are fine.

Usage:
  python3 scrape_burnham_week.py <db.sqlite> [--year 2026] [--delay 2]
      [--dry-run]
"""
import argparse
import csv
import pathlib
import re
import subprocess
import sys
import time

import requests
from bs4 import BeautifulSoup

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

SITE = "https://www.burnhamweek.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0 Safari/537.36 "
                  "(compatible; MarketshareResearchBot/1.0)",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}

# Two heading shapes, and the difference is a whole fleet name:
#   "R1 - 29/08/2026 at 11.40"                    (the combined pages)
#   "R1 - Class 1&2 Fleet - 29/08/2026 at 11.40"  (the per-class pages)
# The middle group is optional and is the better source of the racing class
# than the Fleet column, which is blank on some one-design pages.
RACE_RE = re.compile(
    r"^(R\d+|Race\s*\d+)\s*-\s*(?:(.*?)\s*-\s*)?(\d{2})/(\d{2})/(\d{4})", re.I)
SERIES = {"BH": "Bank Holiday", "MW": "Mid Week", "TD": "Trophy Day",
          "WP": "Week's Points", "JT": "John Torrance"}


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip())


def series_of(url):
    m = re.search(r"/(\d{4})(BH|MW|TD|WP|JT)", url)
    return SERIES.get(m.group(2), "Burnham Week") if m else "Burnham Week"


def index_pages(year):
    html = requests.get(f"{SITE}/results-{year}/", headers=HEADERS, timeout=30).text
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.search(rf"/{year}results/.*\.htm$", href, re.I):
            if not href.startswith("http"):
                href = SITE + href
            # the site links the same page under both burnhamweek.com and
            # www.burnhamweek.com; one host, one page
            href = href.replace("https://burnhamweek.com", SITE)
            if href not in out:
                out.append(href)
    return out


def parse(html):
    """-> (title, [{label, date, fleet_hint, rows}])"""
    soup = BeautifulSoup(html, "html.parser")
    heads = soup.find_all(["h1", "h2", "h3", "h4"])
    title = norm(heads[0].get_text(" ", strip=True)) if heads else ""
    races, name = [], None
    for el in soup.find_all(["h1", "h2", "h3", "h4", "table"]):
        if el.name != "table":
            name = norm(el.get_text(" ", strip=True))
            continue
        if not name:
            continue
        heading, name = name, None
        rows = el.find_all("tr")
        if not rows:
            continue
        head = [norm(c.get_text(" ", strip=True)) for c in rows[0].find_all(["th", "td"])]
        data = []
        for tr in rows[1:]:
            cells = [norm(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
            if len(cells) == len(head):
                data.append(dict(zip(head, cells)))
        if not data:
            continue
        m = RACE_RE.match(heading)
        if m:
            races.append({"label": m.group(1).upper(),
                          "fleet": re.sub(r"\s*Fleet$", "", norm(m.group(2) or "")),
                          "date": f"{m.group(5)}-{m.group(4)}-{m.group(3)}",
                          "rows": data})
        elif re.match(r"^(Overall|.*Fleet)$", heading, re.I):
            # Some pages publish only the standings - every Squib result of
            # the Bank Holiday series is one table and no races. Kept as a
            # standings row rather than dropped, or the boats never exist.
            # "Overall" is not a class, it is the word above a standings
            # table. Left blank so the class falls back to the Fleet column.
            fleet = "" if heading.lower() == "overall" else re.sub(r"\s*Fleet$", "", heading)
            races.append({"label": "Series Standings", "fleet": fleet,
                          "date": None, "rows": data})
    return title, races


def to_csv_rows(rows):
    out = []
    for r in rows:
        sail = re.sub(r"\s+", "", r.get("SailNo") or "").upper()
        if not sail:
            continue
        out.append({
            "Position": r.get("Rank", ""),
            "Points": r.get("Points", ""),
            "SailNo": sail,
            "Boat": r.get("Boat", ""),
            # "Class" is the boat type here; the racing class is "Fleet".
            "BoatType": r.get("Class", ""),
            "Owner": "",
            "SailedBy": r.get("HelmName", ""),
            "FinishTime": r.get("Finish", ""),
            "Elapsed": r.get("Elapsed", ""),
            # YTC is a Portsmouth number, not a rating. Left empty on purpose.
            "Handicap": "",
            "Corrected": r.get("Corrected", ""),
            "Comments": "",
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
           "--source-url", url,
           *(["--race-date", race_date] if race_date else []),
           "--category", "Club", "--source", "scrape:burnham-week"]
    if cls:
        cmd += ["--class", cls]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"    LOAD FAILED: {res.stderr.strip()[:200]}")
        return False
    print(f"    {res.stdout.strip()}")
    return True


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--year", type=int, default=2026)
    p.add_argument("--regatta", default="Burnham Week")
    p.add_argument("--delay", type=float, default=2)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    pages = index_pages(a.year)
    print(f"{len(pages)} result page(s) linked from /results-{a.year}/")

    exports = REPO_ROOT / "exports"
    exports.mkdir(exist_ok=True)
    n_races = n_entries = n_skipped = 0

    for i, url in enumerate(pages):
        if i:
            time.sleep(a.delay)
        try:
            html = requests.get(url, headers=HEADERS, timeout=30).text
        except Exception as e:
            print(f"  {url.rsplit('/', 1)[-1]}: FETCH FAILED {type(e).__name__}")
            continue
        title, races = parse(html)
        if "Page not found" in title or not races:
            n_skipped += 1
            continue
        series = series_of(url)
        print(f"\n[{series}] {title[:60]}")
        for race in races:
            rows = to_csv_rows(race["rows"])
            if not rows:
                continue
            # The fleet is on the rows, not the heading, and one page can hold
            # several ("Class 1,2 & 5 Overall" carries Class 1&2 and Class 5).
            fleets = {r.get("Fleet") for r in race["rows"] if r.get("Fleet")}
            # NEVER blank. The exporter keeps an entry with no class label,
            # on the reasoning that unlabelled rows are mostly RORC IRC
            # racing - which is true there and false here. Left empty, the
            # combined pages put 13 Portsmouth-Yardstick boats into an
            # IRC-scoped dashboard. A combined page gets the fleets it
            # actually holds, so the label is both honest and unmatchable
            # by IRC_DIVISION_RE.
            cls = (race["fleet"] or (sorted(fleets)[0] if len(fleets) == 1 else "")
                   or ("PY " + "/".join(sorted(fleets)) if fleets else "PY combined"))
            race_name = f"{series} {race['label']} - {title[:46]}"
            safe = re.sub(r"[^\w\- ]", "_", f"{a.year}_{series}_{title[:30]}_{race['label']}")[:110]
            out = exports / f"burnham_{safe}.csv"
            write_csv(rows, out, f"{race_name} - {race['date']} - {len(rows)} boats - {url}")
            print(f"  {race['label']:16} {str(race['date'] or '-'):11} {len(rows):3} boats"
                  f"  cls={cls or '(mixed)':14} -> {out.name[:52]}")
            n_races += 1
            n_entries += len(rows)
            if not a.dry_run:
                load(a.db, out, a.regatta, a.year, race_name, cls, url, race["date"])

    print(f"\n{n_races} race(s), {n_entries} entr(y/ies), {n_skipped} empty page(s)"
          + ("  (dry run - nothing loaded)" if a.dry_run else ""))
    print("Reminder: none of this is IRC - see the module docstring.")


if __name__ == "__main__":
    main()
