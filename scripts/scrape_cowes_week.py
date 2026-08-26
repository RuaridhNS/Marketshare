#!/usr/bin/env python3
"""
Real scraper for Cowes Week's published results
(cowesweek.co.uk/web/code/php/main_c.php). Confirmed genuinely scrapable:
robots.txt is a 404 (no restrictions declared), and despite the site
looking JS-driven in a browser, the day/class results picker is a plain
GET request under the hood - no browser automation needed.

The base results page for a season (page=results<year>, no day/class
picked) embeds the FULL day x class schedule as a JS array:
  results[r]=new Array(<classId>,<dayNum>,<raceId>,"<Class Name>");
which is all we need to discover every race without guessing - one
request per season covers the whole schedule.

Each individual result is then:
  ?section=racing&page=results<year>&showentrants=1
   &dayrequest=<dayNum>&classrequest=<classId>/<raceId>
Boat name is inside the row's <a>, owner inside <span class="entrant">
in the same cell - showentrants=1 is what makes the owner span appear.

Usage:
  python3 scrape_cowes_week.py <db.sqlite> <year> [--limit N] [--delay 3]
      [--dry-run]
"""
import sys
import re
import csv
import time
import argparse
import subprocess

import requests
from bs4 import BeautifulSoup

BASE = "https://www.cowesweek.co.uk/web/code/php/main_c.php"
USER_AGENT = "Mozilla/5.0 (compatible; MarketshareResearchBot/1.0)"
DELAY = 3  # seconds; robots.txt declares no restrictions/crawl-delay for this site

SCRIPT_DIR = __import__("pathlib").Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent


def fetch(params):
    r = requests.get(BASE, params=params, timeout=30, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    return r.text


def discover_races(year):
    html = fetch({"map": "cw-res", "style": "std", "ui": "cw-res", "override": "",
                   "section": "racing", "page": f"results{year}"})
    matches = re.findall(r'results\[r\]=new Array\((\d+),(\d+),(\d+),"([^"]*)"\);r\+\+;', html)
    races = []
    for class_id, day_num, race_id, class_name in matches:
        races.append({
            "class_id": class_id, "day_num": int(day_num), "race_id": race_id,
            "class_name": class_name.strip(),
        })
    return races


def fetch_race_results(year, race):
    html = fetch({
        "map": "cw-res", "style": "std", "ui": "cw-res", "override": "",
        "section": "racing", "page": f"results{year}", "showentrants": "1",
        "dayrequest": str(race["day_num"]), "classrequest": f"{race['class_id']}/{race['race_id']}",
        "submit": "",
    })
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if len(tables) < 2:
        return []
    rows = tables[1].find_all("tr")
    if len(rows) < 2:
        return []
    header = [c.get_text(strip=True).lower() for c in rows[0].find_all(["th", "td"])]
    if "sail" not in header:
        return []

    out = []
    for tr in rows[1:]:
        cells = tr.find_all(["th", "td"])
        if len(cells) < len(header):
            continue
        text = [c.get_text(strip=True) for c in cells]
        sail_no = text[header.index("sail")]
        if not sail_no:
            continue
        name_cell = cells[header.index("name")]
        a = name_cell.find("a")
        boat_name = a.get_text(strip=True) if a else name_cell.get_text(strip=True)
        entrant = name_cell.find("span", class_="entrant")
        owner = entrant.get_text(strip=True) if entrant else ""
        pos_raw = text[header.index("pos")] if "pos" in header else ""
        try:
            position = str(int(pos_raw))
            comments = ""
        except ValueError:
            position = ""
            comments = pos_raw  # e.g. OCS / RET / DNF / DSQ / DNC
        out.append({
            "Position": position,
            "Points": "",
            "SailNo": sail_no.upper().replace(" ", ""),
            "Boat": boat_name,
            "BoatType": "",
            "Owner": owner,
            "SailedBy": "",
            "FinishTime": text[header.index("finished")] if "finished" in header else "",
            "Elapsed": text[header.index("elapsed")] if "elapsed" in header else "",
            "Handicap": text[header.index("tcc")] if "tcc" in header else "",
            "Corrected": text[header.index("corrected")] if "corrected" in header else "",
            "Comments": comments,
        })
    return out


def write_csv(rows, out_path, comment):
    cols = ["Position", "Points", "SailNo", "Boat", "BoatType", "Owner",
            "SailedBy", "FinishTime", "Elapsed", "Handicap", "Corrected", "Comments"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        f.write(f"# {comment}\n")
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in rows:
            w.writerow(row)


def load_into_db(db, csv_path, year, race_name, class_label, source_url):
    cmd = [
        sys.executable, str(SCRIPT_DIR / "load_rorc_csv.py"), db, str(csv_path),
        "--regatta", "Cowes Week", "--year", str(year), "--race-name", race_name,
        "--source-url", source_url, "--source", "scrape:cowes", "--category", "Club",
    ]
    if class_label:
        cmd += ["--class", class_label]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"    LOAD FAILED: {result.stderr.strip()}")
        return False
    print(f"    {result.stdout.strip()}")
    return True


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("year", type=int)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--delay", type=float, default=DELAY)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    print(f"Discovering races for {args.year}...")
    races = discover_races(args.year)
    if args.limit:
        races = races[: args.limit]
    print(f"Found {len(races)} day/class combination(s) to scrape.")

    exports_dir = REPO_ROOT / "exports"
    exports_dir.mkdir(exist_ok=True)

    for i, race in enumerate(races):
        if i > 0:
            time.sleep(args.delay)
        label = f"{race['class_name']} - Day {race['day_num']}"
        print(f"[{i+1}/{len(races)}] {label}")
        try:
            rows = fetch_race_results(args.year, race)
        except Exception as e:
            print(f"  FETCH/PARSE FAILED: {e}")
            continue
        if not rows:
            print("  no results on file for this day/class - skipping")
            continue

        source_url = (f"{BASE}?section=racing&page=results{args.year}&dayrequest="
                       f"{race['day_num']}&classrequest={race['class_id']}/{race['race_id']}")
        safe_label = re.sub(r"[^\w\- ]", "_", label)
        out_path = exports_dir / f"cowes_{args.year}_{safe_label}.csv"
        write_csv(rows, out_path, f"Cowes Week {args.year} - {label} - {len(rows)} boats - {source_url}")
        print(f"  {len(rows)} boats -> {out_path.name}")
        if not args.dry_run:
            load_into_db(args.db, out_path, args.year, label, race["class_name"], source_url)

    print("Done.")


if __name__ == "__main__":
    main()
