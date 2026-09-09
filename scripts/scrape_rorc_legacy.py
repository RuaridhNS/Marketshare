#!/usr/bin/env python3
"""
Real scraper for RORC's legacy static results archive (rorc.org/raceresults/,
years 2007-2022 - see scripts/README_scraping.md for the scrapability audit).
Unlike the WebFetch-extraction workflow documented there (built for a sandbox
with no direct internet access), this runs a normal requests/BeautifulSoup
scraper directly, since this machine has real internet access.

Do NOT point this at myjog.jog.org.uk or sailracehq.com - both explicitly
opt out of AI crawler access in robots.txt. Only rorc.org/raceresults/ is
in scope.

Usage:
  python3 scrape_rorc_legacy.py <db.sqlite> <year> [--slug-pattern REGEX]
      [--limit N] [--delay 10] [--dry-run]

  --slug-pattern filters which race pages to scrape (regex matched against
  the URL slug, e.g. '^ircoverall\\d+$' for just the numbered IRC Overall
  Mainseries races). Default: every /raceresults/<year>/*.html link found
  on the season index page.
  --dry-run scrapes and writes CSVs to exports/ but does not load them into
  the database (useful for spot-checking before a big backfill).
"""
import sys
import re
import time
import argparse
import sqlite3
import subprocess
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://www.rorc.org"
USER_AGENT = "Mozilla/5.0 (compatible; MarketshareResearchBot/1.0)"
CRAWL_DELAY = 10  # seconds, per rorc.org/robots.txt

# Column-name normalization: RORC's page generator spells some headers
# differently across years/classes (e.g. "FinishingPlace" vs "Finishing
# Place" with a space) - compare on a whitespace/case-insensitive key.
# Only SAIL NO and BOAT are truly required to trust a table as race results;
# everything else (including Handicap/Corrected) is optional, since
# non-IRC-rated one-design classes (Class40, MOCRA...) race level and
# simply don't have a TCC/corrected-time column at all.
COLUMN_ALIASES = {
    "points": "Points", "sailno": "SailNo", "boat": "Boat",
    "typeofboat": "BoatType", "owner": "Owner", "sailedby": "SailedBy",
    "finishtime": "FinishTime", "elapsed": "Elapsed", "handicap": "Handicap",
    "corrected": "Corrected", "finishingplace": "Position",
    "comments": "Comments",
}
REQUIRED_FIELDS = {"SailNo", "Boat"}

# "Entries: 447     Races Sailed: 12     Discard: 7" - the fleet summary an
# overall-standings page prints where a race page prints its race name.
SUMMARY_LINE = re.compile(r"^\s*Entries:\s*\d", re.I)


def normalize_header_name(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())

KNOWN_CLASSES = [
    "IRC Super Zero", "IRC Zero", "IRC One", "IRC Two", "IRC Three",
    "IRC Four", "IRC Overall", "IRC Two Handed", "IRC2 Hand Nat",
    "Class40", "Class 40", "MOCRA", "Multihull",
]

SCRIPT_DIR = __import__("pathlib").Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent


def fetch(url):
    r = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    return r.text


def discover_race_urls(year, slug_pattern=None):
    index_url = f"{BASE}/racing/race-results/{year}-results"
    html = fetch(index_url)
    soup = BeautifulSoup(html, "html.parser")
    urls = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.search(rf"/raceresults/{year}/([\w\-]+)\.html", href)
        if not m:
            continue
        slug = m.group(1)
        if slug_pattern and not re.search(slug_pattern, slug):
            continue
        urls.add(urljoin(BASE, href))
    return sorted(urls)


def parse_race_page(url):
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")

    body = soup.find("body")
    texts = [t.strip() for t in body.stripped_strings if t.strip()]
    series_title = texts[0] if len(texts) > 0 else ""
    race_name = texts[1] if len(texts) > 1 else ""
    date_line = texts[2] if len(texts) > 2 else ""

    # An "overall standings" page (slug ending -os) has no race on it, so the
    # line where a race page carries its name carries a fleet summary instead.
    # Taking it verbatim produced 251 races in the database called things like
    # "Entries: 447\xa0\xa0\xa0\xa0\xa0Races Sailed: 12" - 12,636 entries under
    # names that are not names. The class is already parsed out of the title,
    # so the page gets the one name that describes it.
    if SUMMARY_LINE.match(race_name):
        race_name = "Season Standings"

    year_m = re.match(r"^(\d{4})\s+(.*)$", series_title)
    rest = year_m.group(2) if year_m else series_title
    class_label = None
    regatta_name = rest
    for cls in KNOWN_CLASSES:
        if rest.endswith(cls):
            class_label = cls
            regatta_name = rest[: -len(cls)].strip()
            break

    date_m = re.search(r"Start:\s*(.+)$", date_line)
    race_date_text = date_m.group(1).strip() if date_m else None

    # Find the results table: the one whose header row contains at least
    # SailNo + Boat once normalized. Everything else in COLUMN_ALIASES is
    # picked up if present and left blank otherwise (see module docstring
    # for why - one-design/box-rule classes have no TCC/Corrected column).
    target_table = None
    field_index = None
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        candidate_header = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
        normalized = [normalize_header_name(h) for h in candidate_header]
        fi = {}
        for i, key in enumerate(normalized):
            field = COLUMN_ALIASES.get(key)
            if field and field not in fi:  # first occurrence wins (some pages repeat Points at the end)
                fi[field] = i
        if REQUIRED_FIELDS.issubset(fi.keys()):
            target_table = table
            field_index = fi
            break

    if target_table is None:
        return {
            "series_title": series_title, "race_name": race_name,
            "regatta_name": regatta_name, "class_label": class_label,
            "race_date_text": race_date_text, "rows": [], "url": url,
        }

    data_rows = target_table.find_all("tr")[1:]
    max_idx = max(field_index.values())
    parsed_rows = []
    for tr in data_rows:
        cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
        if len(cells) <= max_idx:
            continue
        parsed_rows.append({
            field: cells[i] for field, i in field_index.items()
        })

    return {
        "series_title": series_title, "race_name": race_name,
        "regatta_name": regatta_name, "class_label": class_label,
        "race_date_text": race_date_text, "rows": parsed_rows, "url": url,
    }


def write_csv(rows, out_path, meta):
    import csv
    cols = ["Position", "Points", "SailNo", "Boat", "BoatType", "Owner",
            "SailedBy", "FinishTime", "Elapsed", "Handicap", "Corrected",
            "Comments"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        f.write(f"# {meta['series_title']} - {meta['race_name']} "
                f"({meta['race_date_text']}) - {len(rows)} boats - {meta['url']}\n")
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in rows:
            w.writerow(row)


def load_into_db(db, csv_path, meta, year):
    cmd = [
        sys.executable, str(SCRIPT_DIR / "load_rorc_csv.py"), db, str(csv_path),
        "--regatta", meta["regatta_name"] or "RORC", "--year", str(year),
        "--race-name", meta["race_name"] or meta["url"], "--source-url", meta["url"],
    ]
    if meta["class_label"]:
        cmd += ["--class", meta["class_label"]]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  LOAD FAILED: {result.stderr.strip()}")
        return False
    print(f"  {result.stdout.strip()}")
    return True


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("year", type=int)
    p.add_argument("--slug-pattern", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--delay", type=float, default=CRAWL_DELAY)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--resume", action="store_true",
                   help="skip pages already fetched (by races.source_url, or by "
                        "a CSV of the same name left in exports/)")
    args = p.parse_args()

    print(f"Discovering race URLs for {args.year}...")
    urls = discover_race_urls(args.year, args.slug_pattern)
    print(f"Found {len(urls)} race page(s) on the {args.year} index.")

    exports_dir = REPO_ROOT / "exports"
    exports_dir.mkdir(exist_ok=True)

    if args.resume:
        # Two records of what has already been fetched, because the older runs
        # left only one of them. races.source_url is the durable answer going
        # forward; for everything loaded before that was recorded, the CSV the
        # scraper writes on its way into the database is the only trace, and
        # those live in exports/ (gitignored, so they can vanish - which is why
        # the URL is now stored too).
        done = set()
        try:
            con = sqlite3.connect(args.db)
            done |= {u for (u,) in con.execute(
                "SELECT source_url FROM races WHERE IFNULL(source_url,'') <> ''")}
            con.close()
        except sqlite3.Error:
            pass
        before = len(urls)
        kept = []
        for u in urls:
            slug = re.search(rf"/raceresults/{args.year}/([\w\-]+)\.html", u).group(1)
            if u in done or (exports_dir / f"rorc_{args.year}_{slug}.csv").exists():
                continue
            kept.append(u)
        urls = kept
        print(f"  resuming: {before - len(urls)} already fetched, {len(urls)} to go")

    # After the resume filter, so --limit means "fetch this many NEW pages"
    # rather than "look at this many of the index", which with --resume could
    # be a slice that is entirely already-fetched and so do nothing at all.
    if args.limit:
        urls = urls[: args.limit]
        print(f"  limited to the first {len(urls)}")

    for i, url in enumerate(urls):
        if i > 0:
            time.sleep(args.delay)
        slug = re.search(rf"/raceresults/{args.year}/([\w\-]+)\.html", url).group(1)
        print(f"[{i+1}/{len(urls)}] {slug}")
        try:
            meta = parse_race_page(url)
        except Exception as e:
            print(f"  FETCH/PARSE FAILED: {e}")
            continue

        if not meta["rows"]:
            print("  no results table found / 0 rows - skipping")
            continue

        out_path = exports_dir / f"rorc_{args.year}_{slug}.csv"
        write_csv(meta["rows"], out_path, meta)
        print(f"  {meta['regatta_name']!r} / {meta['class_label']!r} / "
              f"{meta['race_name']!r} - {len(meta['rows'])} boats -> {out_path.name}")

        if not args.dry_run:
            load_into_db(args.db, out_path, meta, args.year)

    print("Done.")


if __name__ == "__main__":
    main()
