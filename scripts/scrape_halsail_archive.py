#!/usr/bin/env python3
"""
Scraper for HalSail's *archive* site (archive.halsail.com), where clubs'
past-season results move once they drop off the live site.

The live site (scrape_hamble.py) only ever exposes the current season - old
/Result/Public/<id> links return "Cannot find the series for this request".
The archive is a separate app with its own drill-down API:

    /Result/_CrsResultSetDropDown/<clubId>?DSKey=<anyKnownDsKey>
        -> the club's archived datasets, one per season
    /Result/_CrsClassDropDown/<dsKey>
        -> classes within a dataset ("IRC 1", "Folkboat", ...)
    /Result/_CrsSeryDropDown/<dsKey>?ClassKey=<k>
        -> series within that class
    /Result/_CrsResults/<dsKey>?SeriesKey=<s>
        -> the results HTML: a standings table then one table per race

Note the club dropdown lists only the general per-season datasets. Big one-off
regattas get their own dataset that is NOT listed there (the Taittinger 2025
regatta is dsKey 3415, which no dropdown will hand you) - those have to be
given explicitly with --dskey, taken from the club's own website links.

archive.halsail.com serves no robots.txt (404), so nothing is declared
off-limits; this still avoids the /Result/Print* paths that the main
HalSail.com robots.txt disallows, and keeps a courteous delay.

Results table columns (slightly different from the live site - "Sail number"
rather than "Sail"):
    standings: Rank, Sail Number, Type, Name, Owner, Helm, Club, R1..Rn, Net Pts
    per race:  Place, Sail number, Type, Name, Owner, Helm, Club, Hcap,
               Finish, Elapsed, Corrected, Points

Usage:
  python3 scrape_halsail_archive.py <db.sqlite> --dskey 3415 \
      --regatta "Taittinger Royal Solent Regatta" [--year 2025]
      [--only-irc] [--delay 4] [--dry-run]
  python3 scrape_halsail_archive.py <db.sqlite> --club 1321 --seed-dskey 3414 --list
"""
import sys
import re
import csv
import time
import html as htmlmod
import argparse
import subprocess

import requests
from bs4 import BeautifulSoup

ARCHIVE = "https://archive.halsail.com"
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0 Safari/537.36 "
                  "(compatible; MarketshareResearchBot/1.0)",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}
MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

SCRIPT_DIR = __import__("pathlib").Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent


def fetch(path, referer=None, attempts=4):
    headers = dict(BROWSER_HEADERS)
    headers["Referer"] = referer or f"{ARCHIVE}/"
    last = None
    for n in range(attempts):
        try:
            r = requests.get(ARCHIVE + path, headers=headers, timeout=30)
            r.raise_for_status()
            return r.text
        except Exception as e:
            last = e
            if n < attempts - 1:
                wait = 3 * (2 ** n)
                print(f"    (retry {n+1}/{attempts-1} in {wait}s: {type(e).__name__})", flush=True)
                time.sleep(wait)
    raise last


def options(page_html):
    return [(v, htmlmod.unescape(re.sub(r"<[^>]+>", "", t)).strip())
            for v, t in re.findall(r'<option[^>]*value="([^"]*)"[^>]*>(.*?)</option>',
                                   page_html, re.S)]


def norm_sailno(raw):
    s = (raw or "").replace(" ", "").upper().strip()
    if s and s[0].isdigit():
        return "GBR" + s
    return s


def list_datasets(club_id, seed_dskey):
    return options(fetch(f"/Result/_CrsResultSetDropDown/{club_id}?DSKey={seed_dskey}"))


def parse_results(dskey, series_key, referer=None):
    """-> {'races': [{label, caption, race_date, year, rows:[dict]}], 'title': str}"""
    soup = BeautifulSoup(fetch(f"/Result/_CrsResults/{dskey}?SeriesKey={series_key}",
                               referer=referer), "html.parser")
    title = None
    races = []
    for t in soup.find_all("table"):
        cap_el = t.find("caption")
        cap = re.sub(r"\s+", " ", cap_el.get_text(" ", strip=True)) if cap_el else ""
        rows = t.find_all("tr")
        if not rows:
            continue
        header = [c.get_text(" ", strip=True).lower().replace("- ", "").replace(" ", "")
                  for c in rows[0].find_all(["th", "td"])]
        data = []
        for tr in rows[1:]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if len(cells) == len(header):
                data.append(dict(zip(header, cells)))
        if not data:
            continue
        m = re.match(r"^\s*Race\s+(\d+)", cap, re.I)
        if not m:
            # the standings table - grab the series/class title from its caption
            if title is None and cap:
                title = cap
            continue
        dm = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](20\d\d)", cap)
        race_date = year = None
        if dm:
            year = int(dm.group(3))
            race_date = f"{year:04d}-{int(dm.group(2)):02d}-{int(dm.group(1)):02d}"
        else:
            dm2 = re.search(r"(\d{1,2})\s+([A-Za-z]{3})\w*\s+(20\d\d)", cap)
            if dm2:
                year = int(dm2.group(3))
                mo = MONTHS.get(dm2.group(2).title())
                if mo:
                    race_date = f"{year:04d}-{mo:02d}-{int(dm2.group(1)):02d}"
        races.append({"label": f"Race {m.group(1)}", "caption": cap,
                      "race_date": race_date, "year": year, "rows": data})
    return {"races": races, "title": title}


def race_to_csv_rows(race):
    out = []
    for row in race["rows"]:
        sail = norm_sailno(row.get("sailnumber") or row.get("sail") or row.get("sailno"))
        if not sail:
            continue
        out.append({
            "Position": row.get("place", ""),
            "Points": row.get("points", ""),
            "SailNo": sail,
            "Boat": row.get("name", ""),
            "BoatType": row.get("type", ""),
            "Owner": row.get("owner", ""),
            "SailedBy": row.get("helm", ""),
            "FinishTime": row.get("finish", ""),
            "Elapsed": row.get("elapsed", ""),
            "Handicap": row.get("hcap", ""),
            "Corrected": row.get("corrected", ""),
            "Comments": "",
        })
    return out


def write_csv(rows, out_path, comment):
    cols = ["Position", "Points", "SailNo", "Boat", "BoatType", "Owner",
            "SailedBy", "FinishTime", "Elapsed", "Handicap", "Corrected", "Comments"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        f.write(f"# {comment}\n")
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def load_into_db(db, csv_path, regatta, year, race_name, class_label, source_url):
    cmd = [sys.executable, str(SCRIPT_DIR / "load_rorc_csv.py"), db, str(csv_path),
           "--regatta", regatta, "--year", str(year), "--race-name", race_name,
           "--source-url", source_url, "--category", "Club",
           "--source", "scrape:halsail-archive"]
    if class_label:
        cmd += ["--class", class_label]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"    LOAD FAILED: {res.stderr.strip()}")
        return False
    print(f"    {res.stdout.strip()}")
    return True


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--dskey", help="archive dataset key to scrape")
    p.add_argument("--club", help="club id, for --list")
    p.add_argument("--seed-dskey", help="any known dsKey for that club, for --list")
    p.add_argument("--list", action="store_true", help="list the club's archived seasons and exit")
    p.add_argument("--regatta", help="regatta name to file results under")
    p.add_argument("--year", type=int, help="override the season year")
    p.add_argument("--only-irc", action="store_true", default=True)
    p.add_argument("--all-classes", dest="only_irc", action="store_false")
    p.add_argument("--delay", type=float, default=4)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if args.list:
        if not (args.club and args.seed_dskey):
            sys.exit("--list needs --club and --seed-dskey")
        for v, t in list_datasets(args.club, args.seed_dskey):
            print(f"  dsKey={v:8} {t}")
        return

    if not (args.dskey and args.regatta):
        sys.exit("need --dskey and --regatta (or --list)")

    ref = f"{ARCHIVE}/Result/Public/{args.dskey}/1"
    classes = options(fetch(f"/Result/_CrsClassDropDown/{args.dskey}", referer=ref))
    if args.only_irc:
        classes = [(v, t) for v, t in classes if re.search(r"\bIRC", t, re.I)]
    print(f"dsKey {args.dskey}: {len(classes)} class(es) to scrape")
    for v, t in classes:
        print(f"   {v:5} {t}")

    exports = REPO_ROOT / "exports"
    exports.mkdir(exist_ok=True)
    total = 0

    for i, (ckey, cname) in enumerate(classes):
        if i:
            time.sleep(args.delay)
        try:
            series = options(fetch(f"/Result/_CrsSeryDropDown/{args.dskey}?ClassKey={ckey}", referer=ref))
        except Exception as e:
            print(f"  {cname}: series lookup FAILED {type(e).__name__}")
            continue
        for skey, sname in series:
            time.sleep(args.delay)
            print(f"[{cname}] {sname}")
            try:
                res = parse_results(args.dskey, skey, referer=ref)
            except Exception as e:
                print(f"  FETCH/PARSE FAILED: {type(e).__name__}: {e}")
                continue
            if not res["races"]:
                print("  no per-race tables - skipping")
                continue
            for race in res["races"]:
                rows = race_to_csv_rows(race)
                if not rows:
                    continue
                year = args.year or race["year"]
                if not year:
                    print(f"  {race['label']}: no date - skipping")
                    continue
                race_name = f"{sname} {cname} - {race['label']}"
                if race["race_date"]:
                    race_name += f" ({race['race_date']})"
                safe = re.sub(r"[^\w\- ]", "_", f"{args.dskey}_{cname}_{skey}_{race['label']}")[:120]
                out = exports / f"halarch_{safe}.csv"
                src = f"{ARCHIVE}/Result/Public/{args.dskey}/{skey}"
                write_csv(rows, out, f"{race_name} - {len(rows)} boats - {src}")
                print(f"  {race['label']} [{cname}] - {len(rows)} boats -> {out.name}")
                total += 1
                if not args.dry_run:
                    load_into_db(args.db, out, args.regatta, year, race_name, cname, src)

    print(f"Done. {total} race table(s).")


if __name__ == "__main__":
    main()
