#!/usr/bin/env python3
"""
Scraper for Hamble River Sailing Club's Hamble Winter Series and IRC Autumn
Championship results, published via HalSail (halsail.com).

robots.txt (HalSail): permissive apart from /Account/_LoginDialog,
/Account/_RegisterDialog, /Result/PrintRace and /Result/PrintOverall. This
scraper only touches /Result/Club/<id> and /Result/Public/<id>, never the
two disallowed print endpoints.

Page layout of /Result/Public/<seriesId>:
  - <select id="ddRacingClasses">  classId -> class name ("HWS IRC1",
    "Autumn Regatta IRC2", "J/70", ...)
  - <select id="dd<classId>">      seriesId -> series name, one per class
  - table[0]                       series standings, caption "<class>, <series>"
  - table.R<raceId>                per-race results, caption "Race N, 5 Oct 2025 ..."

Two quirks:
  1. Each per-race table is emitted ~5x over (responsive layout variants),
     so tables are de-duplicated by their R<raceId> class before loading.
  2. halsail.com/Result/Public/<id> 302s to a kxcdn.com mirror; requests
     follows that automatically, but the Referer has to look browser-like.

Unlike the Warsash Sailwave pages, HalSail publishes a real Owner column
(plus Hcap/TCC), which is why this source is worth the extra handling.

Usage:
  python3 scrape_hamble.py <db.sqlite> [--club 3560] [--delay 5] [--dry-run]
      [--only-irc/--all-classes]
"""
import sys
import re
import csv
import time
import argparse
import subprocess

import requests
from bs4 import BeautifulSoup

BASE = "https://www.halsail.com"
HAMBLE_CLUB_ID = 3560
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0 Safari/537.36 "
                  "(compatible; MarketshareResearchBot/1.0)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}
DELAY = 5

MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

SCRIPT_DIR = __import__("pathlib").Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent


def fetch(url, referer=None, attempts=4):
    headers = dict(BROWSER_HEADERS)
    if referer:
        headers["Referer"] = referer
    last = None
    for n in range(attempts):
        try:
            r = requests.get(url, timeout=30, headers=headers, allow_redirects=True)
            r.raise_for_status()
            return r.text
        except Exception as e:
            last = e
            if n < attempts - 1:
                wait = 3 * (2 ** n)
                print(f"    (retry {n+1}/{attempts-1} in {wait}s: {type(e).__name__})")
                time.sleep(wait)
    raise last


def norm_sailno(raw):
    s = (raw or "").replace(" ", "").upper().strip()
    if s and s[0].isdigit():
        return "GBR" + s
    return s


def _discover_once(club_id):
    club_url = f"{BASE}/Result/Club/{club_id}"
    html = fetch(club_url)
    # The club page is usually a 2s JS redirect shim; pull the real result URL
    # out of it. Sometimes the redirect resolves server-side and we land on the
    # result page directly, so only follow the shim when it's actually there.
    if "ddRacingClasses" not in html:
        m = re.search(r'var url = "([^"]+/Result/Public/\d+)"', html)
        if not m:
            raise RuntimeError("club page had neither the class dropdown nor a redirect URL")
        html = fetch(m.group(1), referer=club_url)
    if "ddRacingClasses" not in html:
        raise RuntimeError("result page did not contain the class dropdown")
    return html


def discover_series(club_id, attempts=3):
    """Any one of the club's public result pages carries dropdowns listing every
    class and its series, so a single fetch maps the whole club. This is
    retried and fails loudly - silently returning [] here just looks like
    'this club has no results', which is a much worse failure mode."""
    html = None
    for n in range(attempts):
        try:
            html = _discover_once(club_id)
            break
        except Exception as e:
            if n == attempts - 1:
                raise
            print(f"  (discovery retry {n+1}/{attempts-1}: {e})")
            time.sleep(4 * (n + 1))
    soup = BeautifulSoup(html, "html.parser")

    classes = {}
    dd = soup.find("select", id="ddRacingClasses")
    if dd:
        for opt in dd.find_all("option"):
            classes[opt.get("value")] = opt.get_text(strip=True)

    out = []
    for cid, cname in classes.items():
        sel = soup.find("select", id=f"dd{cid}")
        if not sel:
            continue
        for opt in sel.find_all("option"):
            out.append({"class_id": cid, "class_name": cname,
                        "series_id": opt.get("value"),
                        "series_name": opt.get_text(strip=True)})
    return out


def parse_series_page(series_id, referer=None):
    # /Result/Public/<id> is only a shell - the tables are pulled in by an ajax
    # call to /Result/_Boat/<id>, which also returns richer columns (Owner AND
    # Helm, Crew, Club). Not one of the two /Result/Print* paths robots.txt
    # disallows.
    url = f"{BASE}/Result/Public/{series_id}"
    data_url = f"{BASE}/Result/_Boat/{series_id}"
    soup = BeautifulSoup(fetch(data_url, referer=referer or url), "html.parser")

    # caption of the standings table names the class and series
    class_name = series_name = None
    first = soup.find("table")
    if first and first.find("caption"):
        cap = re.sub(r"\s+", " ", first.find("caption").get_text(" ", strip=True))
        parts = [p.strip() for p in cap.split(",", 1)]
        class_name = parts[0] if parts else None
        series_name = parts[1] if len(parts) > 1 else None

    races, seen = [], set()
    for t in soup.find_all("table"):
        cm = re.search(r"\bR(\d+)\b", " ".join(t.get("class") or []))
        if not cm:
            continue
        rid = cm.group(1)
        if rid in seen:   # same race repeated for responsive variants
            continue
        cap_el = t.find("caption")
        cap = re.sub(r"\s+", " ", cap_el.get_text(" ", strip=True)) if cap_el else ""
        label_m = re.match(r"^(Race\s+\d+)", cap)
        if not label_m:
            continue
        seen.add(rid)

        date_m = re.search(r"(\d{1,2})\s+([A-Za-z]{3})\w*\s+(20\d\d)", cap)
        race_date = year = None
        if date_m:
            year = int(date_m.group(3))
            mon = MONTHS.get(date_m.group(2).title())
            if mon:
                race_date = f"{year:04d}-{mon:02d}-{int(date_m.group(1)):02d}"

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
        if data:
            races.append({"race_id": rid, "label": label_m.group(1), "caption": cap,
                          "race_date": race_date, "year": year, "rows": data})

    return {"class_name": class_name, "series_name": series_name,
            "races": races, "source_url": url}


def race_to_csv_rows(race):
    out = []
    for row in race["rows"]:
        sail_no = norm_sailno(row.get("sail"))
        if not sail_no:
            continue
        out.append({
            "Position": row.get("place", ""),
            "Points": row.get("points", ""),
            "SailNo": sail_no,
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


def load_into_db(db, csv_path, regatta, year, race_name, class_label, source_url, race_date):
    cmd = [
        sys.executable, str(SCRIPT_DIR / "load_rorc_csv.py"), db, str(csv_path),
        "--regatta", regatta, "--year", str(year), "--race-name", race_name,
        "--source-url", source_url, "--source", "scrape:hamble", "--category", "Club",
    ]
    if class_label:
        cmd += ["--class", class_label]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"    LOAD FAILED: {res.stderr.strip()}")
        return False
    print(f"    {res.stdout.strip()}")
    return True


def regatta_for(class_name, series_name):
    """Hamble runs the Winter Series and the IRC Autumn Championship on the same
    HalSail club account; keep them as separate regattas."""
    blob = f"{class_name or ''} {series_name or ''}".lower()
    if "autumn" in blob:
        return "Hamble IRC Autumn Championship"
    return "Hamble Winter Series"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--club", type=int, default=HAMBLE_CLUB_ID)
    p.add_argument("--delay", type=float, default=DELAY)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--all-classes", action="store_true",
                   help="include one-design classes too (default: IRC only)")
    args = p.parse_args()

    print("Discovering classes/series...")
    series = discover_series(args.club)
    if not args.all_classes:
        # NB: no trailing \b - these classes are named "HWS IRC1"/"Autumn
        # Regatta IRC3", so \bIRC\b would match none of them.
        series = [s for s in series if re.search(r"\bIRC", s["class_name"] or "", re.I)]
    print(f"Found {len(series)} series to scrape.")
    for s in series:
        print(f"  - {s['class_name']!r} / {s['series_name']!r} (id {s['series_id']})")

    exports_dir = REPO_ROOT / "exports"
    exports_dir.mkdir(exist_ok=True)

    for i, s in enumerate(series):
        if i > 0:
            time.sleep(args.delay)
        print(f"[{i+1}/{len(series)}] {s['class_name']} / {s['series_name']}")
        try:
            ev = parse_series_page(s["series_id"], referer=f"{BASE}/Result/Club/{args.club}")
        except Exception as e:
            print(f"  FETCH/PARSE FAILED: {e}")
            continue
        if not ev["races"]:
            print("  no per-race tables found - skipping")
            continue

        class_label = s["class_name"]
        regatta = regatta_for(s["class_name"], s["series_name"])
        for race in ev["races"]:
            rows = race_to_csv_rows(race)
            if not rows:
                continue
            year = race["year"]
            if not year:
                print(f"  {race['label']} - no date found - skipping")
                continue
            race_name = f"{s['series_name']} {class_label} - {race['label']}"
            if race["race_date"]:
                race_name += f" ({race['race_date']})"
            safe = re.sub(r"[^\w\- ]", "_", f"{s['series_id']}_{class_label}_{race['label']}")[:120]
            out_path = exports_dir / f"hamble_{safe}.csv"
            write_csv(rows, out_path, f"{race_name} - {len(rows)} boats - {ev['source_url']}")
            print(f"  {race['label']} [{class_label}] - {len(rows)} boats -> {out_path.name}")
            if not args.dry_run:
                load_into_db(args.db, out_path, regatta, year, race_name,
                             class_label, ev["source_url"], race["race_date"])

    print("Done.")


if __name__ == "__main__":
    main()
