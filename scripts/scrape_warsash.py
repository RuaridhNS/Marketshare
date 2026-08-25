#!/usr/bin/env python3
"""
Scraper for Warsash Sailing Club's Spring Series / Spring Championships
results. Warsash publishes via Sailwave, hosted on sailwave.com, and links
them from year-by-year archive pages on warsashsc.org.uk.

robots.txt check (both hosts permissive for this path):
  - warsashsc.org.uk: only /wp-admin/ and WooCommerce cart paths disallowed;
    /springseries/ is fine.
  - sailwave.com: only /wp-admin/ disallowed; /results/ is fine.

Page layout (one .htm per race day/race, all classes on the same page):
    <h1>The Henri-Lloyd Warsash Spring Championships 2026</h1>
    <h2>Warsash Sailing Club</h2>
    <h3>R1 - IRC 1 Champs - April 18</h3>  <table>...</table>
    <h3>R1 - IRC 2 Champs - April 18</h3>  <table>...</table>
    <h3>R1 - IRC 3 Champs - April 18</h3>  <table>...</table>
    <h3>Scoring codes used</h3>            <table>...</table>   <- skipped

Two Warsash-specific quirks vs the Royal Southern Sailwave pages:
  1. The table's "Class" column is the BOAT TYPE (e.g. "Swan 45", "J109",
     "Quarter Ton"), NOT the racing class. The racing class (IRC 1/2/3)
     lives in the <h3> label instead.
  2. There is a "Helm" column but no "Owner" column. Helm is recorded as
     SailedBy (skipper), deliberately NOT as Owner - in this fleet the helm
     is usually but not always the owner, and inventing owner records from
     helm names would pollute the owners table.

Usage:
  python3 scrape_warsash.py <db.sqlite> --year 2026 [--delay 5] [--dry-run]
"""
import sys
import re
import csv
import time
import argparse
import subprocess

import requests
from bs4 import BeautifulSoup

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0 Safari/537.36 "
                  "(compatible; MarketshareResearchBot/1.0)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}
DELAY = 5  # no Crawl-delay declared on either host; courteous default

# Archive page per season. 2022-2024 publish PDFs rather than Sailwave HTML
# and are handled separately (see load_warsash_pdfs.py).
YEAR_PAGES = {
    2026: "https://warsashsc.org.uk/springseries/black-group-results/",
    2025: "https://warsashsc.org.uk/springseries/2025-2/",
}

SCRIPT_DIR = __import__("pathlib").Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent


def fetch(url, referer=None, attempts=4):
    """Transient DNS/SSL/connection-reset failures have been common on this
    network (a whole Cowes Week year was lost to them), so retry with backoff
    rather than letting one blip kill a long run."""
    headers = dict(BROWSER_HEADERS)
    if referer:
        headers["Referer"] = referer
    last = None
    for n in range(attempts):
        try:
            r = requests.get(url, timeout=30, headers=headers)
            r.raise_for_status()
            return r.text
        except Exception as e:
            last = e
            if n < attempts - 1:
                wait = 3 * (2 ** n)  # 3s, 6s, 12s
                print(f"    (retry {n+1}/{attempts-1} in {wait}s: {type(e).__name__})")
                time.sleep(wait)
    raise last


def norm_sailno(raw):
    """Warsash sail numbers already carry a country prefix (ITA15656, GBR1111X),
    so this only tidies whitespace/case. Bare-numeric ones get GBR, matching
    the convention used by every other loaded source."""
    s = (raw or "").replace(" ", "").upper().strip()
    if s and s[0].isdigit():
        return "GBR" + s
    return s


def discover_result_urls(year):
    page = YEAR_PAGES.get(year)
    if not page:
        return [], None
    html = fetch(page)
    urls = re.findall(r'https://sailwave\.com/results/warsashsc/[^"\'\s]+\.htm', html)
    # de-dupe, keep order stable
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out, page


def parse_results_page(url, referer=None):
    soup = BeautifulSoup(fetch(url, referer=referer), "html.parser")
    body = soup.find("body") or soup

    h1 = body.find("h1")
    event_name = re.sub(r"\s+", " ", h1.get_text(" ", strip=True)) if h1 else url.rsplit("/", 1)[-1]
    ym = re.search(r"\b(20\d\d)\b", event_name) or re.search(r"\b(20\d\d)\b", body.get_text())
    season_year = int(ym.group(1)) if ym else None

    races = []
    label = None
    for el in body.find_all(["h3", "table"]):
        if el.name == "h3":
            # \xa0 shows up as the dash separator in these labels
            label = re.sub(r"\s+", " ", el.get_text(" ", strip=True).replace("\xa0", " ")).strip()
            continue
        if el.name != "table" or not label:
            continue
        if "scoring code" in label.lower():
            label = None
            continue
        # Only per-race tables. The *_Sum.htm pages repeat the same fleet as
        # series standings under labels like "IRC 1 Champs" (no "R<n> -"
        # prefix); loading those too would give every boat a phantom extra
        # entry on top of the races it actually sailed.
        if not re.match(r"^R\d+\b", label):
            label = None
            continue

        rows = el.find_all("tr")
        if not rows:
            label = None
            continue
        header = [c.get_text(strip=True).lower() for c in rows[0].find_all(["th", "td"])]
        data = []
        for tr in rows[1:]:
            cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
            if len(cells) == len(header):
                data.append(dict(zip(header, cells)))
        if not data:
            label = None
            continue

        # Racing class comes from the h3 label ("R1 - IRC 2 Champs - April 18"),
        # since this page's "Class" COLUMN is the boat type instead.
        cm = re.search(r"\b(IRC\s*\d+|IRC\s*Zero)\b", label, re.I)
        class_label = re.sub(r"\s+", " ", cm.group(1)).upper() if cm else None
        races.append({"label": label, "rows": data, "class_label": class_label})
        label = None

    return {"event_name": event_name, "season_year": season_year,
            "races": races, "source_url": url}


def race_to_csv_rows(race):
    out = []
    for row in race["rows"]:
        sail_no = norm_sailno(row.get("sailno") or row.get("sail no") or row.get("sail number"))
        if not sail_no:
            continue
        out.append({
            "Position": row.get("rank", ""),
            "Points": row.get("points", ""),
            "SailNo": sail_no,
            "Boat": row.get("boat", "") or row.get("boat name", ""),
            # NB: Sailwave's "class" column here is the boat type
            "BoatType": row.get("class", ""),
            "Owner": "",
            "SailedBy": row.get("helm", ""),
            "FinishTime": row.get("finish", ""),
            "Elapsed": row.get("elapsed", ""),
            "Handicap": row.get("rating", ""),
            "Corrected": row.get("corrected", ""),
            "Comments": row.get("code", "") or row.get("comments", ""),
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
    cmd = [
        sys.executable, str(SCRIPT_DIR / "load_rorc_csv.py"), db, str(csv_path),
        "--regatta", regatta, "--year", str(year), "--race-name", race_name,
        "--source-url", source_url, "--category", "Club",
    ]
    if class_label:
        cmd += ["--class", class_label]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"    LOAD FAILED: {res.stderr.strip()}")
        return False
    print(f"    {res.stdout.strip()}")
    return True


def regatta_for(event_name):
    """Warsash runs two distinct competitions; keep them as separate regattas
    so their entry trends don't get merged."""
    if re.search(r"champ", event_name, re.I):
        return "Warsash Spring Championships"
    return "Warsash Spring Series"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--delay", type=float, default=DELAY)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    urls, archive_page = discover_result_urls(args.year)
    if not urls:
        print(f"No Sailwave result pages found for {args.year}. "
              f"(2022-2024 publish PDFs, not Sailwave HTML.)")
        return
    if args.limit:
        urls = urls[: args.limit]
    print(f"Found {len(urls)} result page(s) for {args.year}.")

    exports_dir = REPO_ROOT / "exports"
    exports_dir.mkdir(exist_ok=True)

    for i, url in enumerate(urls):
        if i > 0:
            time.sleep(args.delay)
        name = url.rsplit("/", 1)[-1]
        print(f"[{i+1}/{len(urls)}] {name}")
        try:
            ev = parse_results_page(url, referer=archive_page)
        except Exception as e:
            print(f"  FETCH/PARSE FAILED: {e}")
            continue
        if not ev["races"]:
            print("  no per-race tables (likely a series-summary page) - skipping")
            continue
        year = ev["season_year"] or args.year
        regatta = regatta_for(ev["event_name"])

        for race in ev["races"]:
            rows = race_to_csv_rows(race)
            if not rows:
                continue
            race_name = f"{ev['event_name']} - {race['label']}"
            safe = re.sub(r"[^\w\- ]", "_", f"{name[:-4]}_{race['label']}")[:120]
            out_path = exports_dir / f"warsash_{safe}.csv"
            write_csv(rows, out_path, f"{race_name} - {len(rows)} boats - {url}")
            print(f"  {race['label']!r} [{race['class_label'] or '?'}] - {len(rows)} boats -> {out_path.name}")
            if not args.dry_run:
                load_into_db(args.db, out_path, regatta, year, race_name,
                             race["class_label"], url)

    print("Done.")


if __name__ == "__main__":
    main()
