#!/usr/bin/env python3
"""
Real scraper for Royal Southern Yacht Club's published race results
(scm.royal-southern.co.uk, powered by Sailwave). Confirmed genuinely
scrapable: royal-southern.co.uk's robots.txt is fully permissive, and
scm.royal-southern.co.uk's only disallows deep /events/ listing pages and
/calendar - not the /racing-results/ or /iframe/ paths this uses. Note:
rsyc.org.uk is a DIFFERENT club (Royal Southampton) that explicitly
disallows /sailing/ in robots.txt - never point this at that domain.

Each results page embeds its actual Sailwave-generated table via an
<iframe src="/iframe/<id>">, which needs full browser-like headers (User-
Agent/Accept/Accept-Language) plus a Referer or it 406s - a bot-detection
quirk, not a robots.txt-declared policy.

Page structure inside the iframe: <h1>Event name</h1><h2>Club</h2>
<h3>Overall</h3><table>...</table> <h3>R1</h3><table>...</table>
<h3>R2</h3><table>...</table> ... <h3>Scoring codes used</h3><table>...</table>
- "Overall" is the series standings (skipped here, we load per-race
  results and let the DB derive standings); each "R<n>" is one race.

Sail numbers here have no country prefix (e.g. "8463 R") - normalized to
GBR-prefixed, no-space form ("GBR8463R") to match the convention used by
every other loaded source, so the same boat isn't duplicated across them.

Usage:
  python3 scrape_royal_southern.py <db.sqlite> [--keyword summer-series]
      [--limit N] [--delay 5] [--dry-run]
"""
import sys
import re
import csv
import time
import argparse
import subprocess

import requests
from bs4 import BeautifulSoup

BASE = "https://scm.royal-southern.co.uk"
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36 (compatible; MarketshareResearchBot/1.0)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}
DELAY = 5  # seconds; no Crawl-delay specified in robots.txt, this is a courteous default

SCRIPT_DIR = __import__("pathlib").Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent


def norm_sailno(raw, mna=None):
    s = (raw or "").replace(" ", "").upper().strip()
    mna = (mna or "").strip().upper()
    if mna and s and not s.startswith(mna):
        return mna + s
    if s and s[0].isdigit():
        return "GBR" + s  # no MNA column on this page's layout - assume British fleet
    return s


def fetch(url, referer=None, attempts=4):
    """Retried with backoff: this network drops DNS/SSL/connections often
    enough that a single blip was costing whole scrape runs elsewhere."""
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
                wait = 3 * (2 ** n)
                print(f"    (retry {n+1}/{attempts-1} in {wait}s: {type(e).__name__})", flush=True)
                time.sleep(wait)
    raise last


def discover_result_slugs(keyword=None):
    # Any single racing-results page carries the full sidebar catalog of
    # every result link on the site - fetch one known-stable page to harvest it.
    html = fetch(f"{BASE}/racing-results/4x4-2024")
    soup = BeautifulSoup(html, "html.parser")
    slugs = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.match(r"^/racing-results/([\w\-]+)$", href)
        if not m:
            continue
        slug = m.group(1)
        if keyword and keyword.lower() not in href.lower() and keyword.lower() not in a.get_text(strip=True).lower():
            continue
        slugs.add(slug)
    return sorted(slugs)


def regatta_name_for(event_name, slug=""):
    """Map a results page to the regatta it belongs to, or None to skip it.

    The Summer Series regattas are sponsored, and the sponsor changes year to
    year (North Sails May Regatta, Champagne Charlie June Regatta, Salcombe Gin
    / Key Yachting July Regatta), so they are matched on the month rather than
    the full title. Everything else that is a real IRC keelboat regatta gets
    its own name; junior, dinghy and one-design-only events are skipped, as is
    the "Summer Series" cumulative standing, which is an aggregate of the four
    month regattas rather than a regatta boats enter.
    """
    blob = f"{event_name} {slug}".lower()
    if re.search(r"\bjunior\b|\bcadet\b|\bdinghy\b|\bicebreaker\b|\byouth\b", blob):
        return None
    # One-design class championships the club hosts. The tool is scoped to the
    # IRC fleet, so these are not regattas we track - the handful of their
    # boats that also hold IRC certs still appear via their IRC racing.
    if re.search(r"\b(sb\s?20|j/?70|j/?80|xod|x one design|squib|dragon|etchells|"
                 r"flying\s?15|sonata|folkboat|rs\s?21|rs\s?elite)\b.*\b(national|championship)", blob):
        return None
    month_m = re.search(r"\b(May|June|July|September)\s+Regatta\b", event_name, re.I)
    if month_m:
        return f"Royal Southern {month_m.group(1).title()} Regatta"
    if re.search(r"summer series", blob):
        return None          # cumulative standing across the month regattas
    if re.search(r"charity|match racing", blob):
        return None
    # Named events are matched on a keyword rather than their full title,
    # because the title carries a sponsor that changes year to year
    # ("CompareYachtInsure 4x4 Championships" vs plain "4x4 Championships",
    # "Key Yachting J-Cup"). Without this each sponsor spawned its own regatta
    # and the same event's history fragmented across several records.
    NAMED = [
        (r"\b4\s*x\s*4\b",                 "Royal Southern 4x4 Championships"),
        (r"j[- ]?cup",                     "Royal Southern J-Cup"),
        (r"ancient mariner",               "Royal Southern Ancient Mariners Race"),
        (r"round the isle of wight|rtiw",  "Royal Southern Round the Isle of Wight Double-Handed"),
        (r"early bird",                    "Royal Southern Early Bird Series"),
        (r"spring championship",           "Royal Southern Spring Championship"),
        (r"winter series",                 "Royal Southern Winter Series"),
    ]
    for pat, name in NAMED:
        if re.search(pat, blob):
            return name
    # anything else: strip sponsors, a trailing year, and "- Series II" or
    # "- IRC Class" style suffixes, so one event doesn't fragment into several
    # regattas across seasons.
    name = re.sub(r"\s+", " ", event_name).strip()
    name = re.sub(r"^(key yachting|x-yachts|salcombe gin|north sails|champagne charlie|"
                  r"compareyachtinsure|henri[- ]lloyd)\s+", "", name, flags=re.I)
    name = re.sub(r"\s*[-–]\s*(series\s+[ivx0-9]+|irc(\s+class)?|black group|white group)\s*$", "", name, flags=re.I)
    name = re.sub(r"\s*20\d\d\s*$", "", name).strip()
    name = re.sub(r"\bChampionships\b", "Championship", name)   # singular/plural drift
    if not name:
        return None
    return name if name.lower().startswith("royal southern") else f"Royal Southern {name}"


def parse_event_page(slug):
    page_url = f"{BASE}/racing-results/{slug}"
    html = fetch(page_url)
    soup = BeautifulSoup(html, "html.parser")
    iframe = soup.find("iframe")
    if not iframe or not iframe.get("src"):
        return None
    iframe_url = BASE + iframe["src"] if iframe["src"].startswith("/") else iframe["src"]
    time.sleep(1)  # be polite between the shell page and its iframe
    iframe_html = fetch(iframe_url, referer=page_url)
    isoup = BeautifulSoup(iframe_html, "html.parser")
    body = isoup.find("body") or isoup

    h1 = body.find("h1")
    # some pages style a year/date span with no surrounding whitespace in the
    # source HTML (e.g. "...Series<b>2025</b>September..."); a separator
    # keeps get_text() from jamming adjacent text nodes together
    event_name = re.sub(r"\s+", " ", h1.get_text(" ", strip=True)) if h1 else slug
    year_m = re.search(r"\b(20\d\d)\b", event_name)
    if not year_m:
        # fall back to the "Results are provisional as of ... <Month> <Day>, <Year>" line
        year_m = re.search(r"\b(20\d\d)\b", body.get_text())
    season_year = int(year_m.group(1)) if year_m else None

    page_group = None
    gm = re.search(r"\b(Black|White|Red|Blue|Gold)\s+Group\b", event_name, re.I)
    if gm:
        page_group = gm.group(1).title()

    races = []
    current_label = None
    for el in body.find_all(["h3", "table"], recursive=True):
        if el.name == "h3":
            current_label = el.get_text(strip=True)
            continue
        if el.name == "table" and current_label and re.match(r"^R\d+\b", current_label):
            rows = el.find_all("tr")
            if not rows:
                continue
            header = [c.get_text(strip=True).lower() for c in rows[0].find_all(["th", "td"])]
            data_rows = []
            for tr in rows[1:]:
                cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
                if len(cells) != len(header):
                    continue
                data_rows.append(dict(zip(header, cells)))
            # class: prefer the table's own per-row "Class" column (most accurate);
            # else the h3 label itself, e.g. "R1 - IRC 1 Class" -> "IRC 1"; else the
            # page-title's colour group (e.g. "Black Group")
            row_class = data_rows[0].get("class") if data_rows else None
            label_class_m = re.search(r"^R\d+\s*-\s*(.+?)(?:\s+Class)?$", current_label, re.I)
            label_class = label_class_m.group(1).strip() if label_class_m else None
            races.append({
                "label": current_label, "header": header, "rows": data_rows,
                "class_hint": row_class or label_class or page_group,
            })
            current_label = None

    return {
        "event_name": event_name, "season_year": season_year,
        "races": races, "source_url": page_url, "slug": slug,
    }


def race_to_csv_rows(race):
    out = []
    for row in race["rows"]:
        sail_no = norm_sailno(row.get("sail number"), row.get("boat mna"))
        if not sail_no:
            continue
        comments = row.get("comments") or row.get("code") or ""
        out.append({
            "Position": row.get("rank", ""),
            "Points": row.get("points", ""),
            "SailNo": sail_no,
            "Boat": row.get("boat name") or row.get("boat", ""),
            "BoatType": row.get("boat type", ""),
            "Owner": row.get("owner", ""),
            "SailedBy": "",
            "FinishTime": row.get("finish", ""),
            "Elapsed": row.get("elapsed", ""),
            "Handicap": row.get("irc tcc", ""),
            "Corrected": row.get("corrected", ""),
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


def load_into_db(db, csv_path, regatta, year, race_name, class_label, source_url):
    cmd = [
        sys.executable, str(SCRIPT_DIR / "load_rorc_csv.py"), db, str(csv_path),
        "--regatta", regatta, "--year", str(year), "--race-name", race_name,
        "--source-url", source_url, "--source", "scrape:royal-southern", "--category", "Club",
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
    p.add_argument("--keyword", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--delay", type=float, default=DELAY)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    print("Discovering result page slugs...")
    slugs = discover_result_slugs(args.keyword)
    if args.limit:
        slugs = slugs[: args.limit]
    print(f"Found {len(slugs)} page(s) to scrape.")

    exports_dir = REPO_ROOT / "exports"
    exports_dir.mkdir(exist_ok=True)

    for i, slug in enumerate(slugs):
        if i > 0:
            time.sleep(args.delay)
        print(f"[{i+1}/{len(slugs)}] {slug}")
        try:
            event = parse_event_page(slug)
        except Exception as e:
            print(f"  FETCH/PARSE FAILED: {e}")
            continue
        if not event:
            print("  no iframe/results found - skipping")
            continue
        if not event["races"]:
            print(f"  '{event['event_name']}' - no per-race tables (maybe overall-only page) - skipping")
            continue
        if not event["season_year"]:
            print(f"  '{event['event_name']}' - couldn't determine a season year - skipping")
            continue

        # "Summer Series" is a cumulative points competition across the May/
        # June/July/September Regattas, not itself a place boats race - model
        # each sub-regatta as its own regatta rather than merging them into
        # one "Summer Series" event per year (which would wrongly combine
        # 4 different regattas' races into a single event).
        regatta = regatta_name_for(event["event_name"], slug)
        if regatta is None:
            print(f"  '{event['event_name']}' - not an IRC keelboat regatta - skipping")
            continue
        for race in event["races"]:
            rows = race_to_csv_rows(race)
            if not rows:
                continue
            race_name = f"{event['event_name']} - {race['label']}"
            safe_label = re.sub(r"[^\w\- ]", "_", race["label"])
            out_path = exports_dir / f"rsyc_{event['slug']}_{safe_label}.csv"
            write_csv(rows, out_path, f"{race_name} - {len(rows)} boats - {event['source_url']}")
            print(f"  {race_name!r} - {len(rows)} boats -> {out_path.name}")
            if not args.dry_run:
                load_into_db(args.db, out_path, regatta, event["season_year"], race_name,
                             race["class_hint"], event["source_url"])

    print("Done.")


if __name__ == "__main__":
    main()
