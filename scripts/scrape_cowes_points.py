#!/usr/bin/env python3
"""
Load Cowes Week from the OVERALL SERIES STANDINGS, for the seasons that publish
no daily results.

scrape_cowes_week.py reads the daily results page. Five seasons return nothing
there and so looked absent from the site entirely:

    2007  2008  2009  2015  2018

They are not absent. They publish standings instead, on the page the daily view
links to as "View Overall Results":

    main_c.php?section=racing&page=points<year>&resultrequest=<seriesId>

With no resultrequest the page returns the series picker - a select whose
options are that season's class list with numeric ids (49 classes in 2007, 43
in 2008, 44 in 2009, 51 in 2015, 55 in 2018). 2020 is the cancelled season and
genuinely holds nothing.

THE FIVE SEASONS ARE NOT THE SAME SHAPE, and that decides how much can be
loaded from each. Two things vary, both checked page by page:

    season   scoring codes   boat pages (sail numbers)   loaded as
    2018     yes             yes                         per-race entries
    2015     no              yes                         one entry per class
    2009     no              yes                         one entry per class
    2008     no              NO                          captured, not loaded
    2007     no              NO                          captured, not loaded

WHY THE CODES DECIDE IT. Turning a standings table into per-race entries needs
a way to tell who actually sailed each race, and only the scoring codes give
that. The obvious substitute - "a boat on the maximum score for that column did
not sail" - was tested against 2018, where the codes give ground truth, and it
fails: over 12 classes it gets 9.5% of cells wrong, and wrongly, because a boat
that STARTED and retired is scored exactly the same penalty as one that never
came out. It would have thrown away 124 real entries and invented 8. So for a
season with no codes this script makes no per-race claim at all; it records one
entry per boat per class, in a race called "<class> - Series", carrying the
boat's real overall position and total. That the boat entered that class that
season is a fact the table does prove.

WHY 2007 AND 2008 ARE NOT LOADED AT ALL. Neither publishes boat pages, so
their rows are a name and nothing else. LOKI, PANTHERA, ISLAND FLING and SNOW
LION are already in this database with sail numbers from the RORC legacy
archive, and attaching a name-only row to one of those means resolving a boat
by name - the mistake behind every collision in data/boat_merges.csv. Loading
them anyway would mint roughly 1,200 shadow boats that inflate every count and
that the Duplicates page could not even propose merging, since it needs a known
boat type to corroborate a name and these have none. Their rows are written to
data/cowes_names_<year>.csv for a person to adjudicate instead. --allow-nameless
overrides this.

THE CODE VOCABULARY, for the seasons that publish it. Worked out from the data
rather than assumed, the same way load_rorc_season_points.py had to:

    (blank)  a finishing score          -> an entry
    DNF RET  started, did not finish    -> an entry, status retired
    DSQ      started, disqualified      -> an entry, status disqualified
    DNC      did not compete            -> NOT an entry
    NER      not entered for that race  -> NOT an entry
    NOD      no declaration             -> NOT an entry (see below)

NER is the most common code and it is settled, not guessed. Every boat detail
page carries "Days entered" as an eight-character week (SSMTWTFS = Saturday
through Saturday), and the NER days are exactly the days outside it. COBRA
entered SS____F_ and scored NER on Monday to Thursday; KESTREL and ROCK LOBSTER
entered SS______ and scored NER on the same four; PYR QUOHHA 8 entered
___TWTF_ and scored NER on Saturday, Sunday and Monday. Four for four, from a
field on a different page that cannot have been fitted to the guess.

NOD IS THE ONE JUDGEMENT HERE, and it is deliberately the cautious one. It
carries the same penalty as DNC (fleet size plus one) but appears on days the
boat WAS entered for - SIMPLES entered all eight days and scored DNF, NOD, DNC,
NOD, DNC, NOD. The site offers no key, so whether the boat sailed and failed to
declare, or never came out, is not decidable from here. Loading it as an entry
would invent fleet presence, which is the failure that corrupts a share figure;
leaving it out only understates. So it is left out, and every run prints how
many cells that was, so the size of the doubt stays visible and the call can be
revisited against real numbers.

RACE NAMING. Races are named "<class> - Day <n>" to match what the daily
scraper writes for 2010-2026, so a future daily load of these seasons merges
into these races instead of duplicating them. The day number comes from the
column's weekday, positioned in the fixed Saturday-to-Saturday week, because
the header row is neither ordered nor unique and so cannot be used directly:
2008 lists two Saturdays (the first and last day of the week), and 2007's
Class 0 IRC raced Fri, Wed, Thu in that order. Mapping through the weekday
gives each column a distinct number regardless. 2015 numbers its columns
R1-R7 with no weekday at all, so those keep the source's own numbering and are
named "<class> - R<n>".

Group-level series are SKIPPED. "Black Group Overall" and "White Group
Overall" re-cut the same boats across the class series, exactly like Round the
Island's Double Handed view - loading them alongside the classes would enter
every boat twice and inflate every share figure downstream.

Where boat pages DO exist, identity is real and not inferred: each row links to
boatdetails<year>&boatref=<n>, which carries the sail number, design type,
handicap/TCC, entered-by and skipper - more than the daily scrape gets, which
has neither TCC nor skipper. Those are fetched once per boat and cached under
data/cowes_boats/<year>/, which is what makes an interrupted run cheap to
finish. A few boats have no page even in a season that has them (2018's refs
1333, 1388, 1418 and 1552 return the site chrome and nothing else); those fall
back to a name and are listed by name at the end of the run.

Usage:
  python3 scrape_cowes_points.py <db.sqlite> <year> [--delay 3] [--resume]
      [--dry-run] [--limit N] [--only-class SUBSTR] [--mode auto|races|series]
      [--allow-nameless] [--refetch-empty] [--cache-only]
"""
import os
import re
import sys
import csv
import json
import time
import argparse
import sqlite3
import pathlib

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_db import (get_or_create_owner, get_or_create_boat, get_or_create_regatta,
                      get_or_create_event, create_race, norm, norm_upper)

BASE = "https://www.cowesweek.co.uk/web/code/php/main_c.php"
USER_AGENT = "Mozilla/5.0 (compatible; MarketshareResearchBot/1.0)"
DELAY = 3  # robots.txt declares no restrictions or crawl-delay for this site
REGATTA = "Cowes Week"
SOURCE = "scrape:cowes-points"

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
CACHE_DIR = REPO_ROOT / "data" / "cowes_boats"

# Cowes Week runs Saturday to Saturday, which is how boatdetails writes its
# "Days entered" field: SSMTWTFS. A weekday header is placed in this week to
# get its day number; a second Saturday in one header row is the last day.
WEEK = ["Sat", "Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

# Columns that are not races. "Best5"/"Best6"/"Best3" are discard scores and
# "O/A" is the boat's place in the whole-regatta standings, not a race result.
NOT_A_RACE = re.compile(r"^(Pos|Boat\s*Name|Total|O/A|Best\d*)$", re.I)
RACE_NUMBERED = re.compile(r"^R(\d+)$", re.I)
GROUP_SERIES = re.compile(r"\bgroup\s+overall\b", re.I)

# Worked out from the data - see the module docstring. Anything not listed is
# treated as "did not sail" AND reported, so a code this script has never seen
# cannot quietly become an entry or vanish without a mention.
STARTED_RETIRED = {"DNF", "RET", "RTD", "DNS"}
STARTED_DISQ = {"DSQ", "BFD", "UFD", "OCS", "SCP", "NSC", "DGM"}
DID_NOT_SAIL = {"DNC", "NER", "NOD"}


def clean(t):
    """Strip the encoding damage these pages carry.

    Every cell ends in U+FFFD: the page is served as one encoding and declares
    another, so a trailing non-breaking space arrives as a replacement
    character. Left in, it becomes part of every boat name and class label.
    """
    if t is None:
        return None
    t = t.replace("�", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", t).strip()


def fetch(params, attempts=4):
    """Retried with backoff, for the same reason the daily scraper retries:
    one transient DNS drop mid-run cost 80 of 2017's 348 race fetches, and the
    run still reported success."""
    last = None
    for n in range(attempts):
        try:
            r = requests.get(BASE, params=params, timeout=30,
                             headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            return r.text
        except Exception as e:
            last = e
            if n < attempts - 1:
                wait = 3 * (2 ** n)
                print(f"    (retry {n+1}/{attempts-1} in {wait}s: {type(e).__name__})",
                      flush=True)
                time.sleep(wait)
    raise last


def discover_series(year):
    """Every class series for a season, group-level re-cuts excluded."""
    html = fetch({"section": "racing", "page": f"points{year}"})
    out = []
    for sid, name in re.findall(r'<option[^>]*value="(\d+)"[^>]*>([^<]+)</option>', html):
        name = clean(name)
        if sid == "0" or not name:
            continue
        if GROUP_SERIES.search(name):
            continue
        out.append((sid, name))
    return out


def biggest_table(html):
    soup = BeautifulSoup(html, "html.parser")
    tabs = [t for t in soup.find_all("table") if len(t.find_all("tr")) >= 2]
    return tabs[0] if tabs else None


def parse_standings(html):
    """(race column headers, rows) from one class's standings table.

    A row is (boatref, name, owner, pos, total, [(header, value, code)]). The
    boat name is the link text where there is a link and the cell text where
    there is not - 2007 and 2008 publish no boat pages at all, so their rows
    are plain text and an earlier version of this parser silently returned
    nothing for both seasons. The owner is always a separate node (a
    span.entrant in 2018, a parenthetical in 2015), so no row needs a single
    string split into name and owner.
    """
    table = biggest_table(html)
    if table is None:
        return [], []
    trs = table.find_all("tr")
    header = [clean(c.get_text(" ", strip=True)) for c in trs[0].find_all(["td", "th"])]
    race_idx = [i for i, h in enumerate(header) if h and not NOT_A_RACE.match(h)]
    name_idx = next((i for i, h in enumerate(header) if h and
                     re.match(r"^Boat\s*Name$", h, re.I)), 1)
    pos_idx = next((i for i, h in enumerate(header) if h and
                    re.match(r"^Pos$", h, re.I)), 0)
    total_idx = next((i for i, h in enumerate(header) if h and
                      re.match(r"^Total$", h, re.I)), None)
    rows = []
    for tr in trs[1:]:
        tds = tr.find_all("td")
        if not tds or name_idx >= len(tds):
            continue
        a = tr.find("a", href=re.compile("boatdetails"))
        boatref = None
        if a is not None:
            m = re.search(r"boatref=(\d+)", a["href"])
            boatref = m.group(1) if m else None
            name = clean(a.get_text())
        else:
            # 2007 and 2008: no boat pages, so the name is the cell's own text.
            name = clean(tds[name_idx].get_text(" ", strip=True))
            name = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
        if not name:
            continue                      # separator or summary row
        span = tr.find("span", class_="entrant")
        if span is not None:
            owner = clean(span.get_text())
        else:
            # 2015 writes it as "TEASING MACHINE (Eric De Turckheim)", with the
            # parenthetical outside the link.
            cell = clean(tds[name_idx].get_text(" ", strip=True))
            mm = re.search(r"\(([^)]+)\)\s*$", cell)
            owner = clean(mm.group(1)) if mm else None
        pos = points_of(clean(tds[pos_idx].get_text(" ", strip=True))) \
            if pos_idx < len(tds) else None
        total = points_of(clean(tds[total_idx].get_text(" ", strip=True))) \
            if total_idx is not None and total_idx < len(tds) else None
        cells = []
        for i in race_idx:
            if i >= len(tds):
                continue
            td = tds[i]
            st = td.find("span", class_="status")
            code = clean(st.get_text()).upper() if st is not None else ""
            # The value sits before the <br> that carries the status span.
            head = td.decode_contents().split("<br")[0]
            value = clean(re.sub(r"<[^>]+>", " ", head))
            if not code:
                # 2007-2015 put the code in the cell text instead of a span.
                mm = re.match(r"^([\d.]+)\s+([A-Z]{2,4})$", value or "")
                if mm:
                    value, code = mm.group(1), mm.group(2)
            cells.append((header[i], value, code))
        rows.append((boatref, name, owner, pos, total, cells))
    return [header[i] for i in race_idx], rows


def probe_mode(year, sid):
    """Whether this season can be loaded as races, decided from its own pages.

    Only a season that publishes scoring codes can be: without them there is no
    way to tell who sailed a given race, and the numbers cannot stand in. That
    was tested rather than assumed. Calibrating a "the penalty score means it
    did not sail" rule against 2018, where the codes give ground truth, gets
    9.5% of cells wrong over 12 classes - and wrong systematically, because a
    boat that started and retired scores exactly the same penalty as one that
    never came out. It would have discarded 124 real entries and invented 8.

    The codes live in <span class="status">, which is present in every 2018
    class page and in none from 2007, 2008, 2009 or 2015 (4,001 cells checked
    across 2007-2008 alone, zero codes). Careful with a plain substring test
    for the codes themselves: 'NER' matches MINERVA, which is what first made
    2009 look like it had them.
    """
    html = fetch({"section": "racing", "page": f"points{year}",
                  "resultrequest": sid})
    return ("races" if 'class="status"' in html else "series"), html


def boat_details(year, boatref, delay, cache_only=False, refetch_empty=False):
    """Sail number, type, rating, owner and skipper for one boat, cached.

    One fetch per boat per season is the bulk of the run, and a season is
    600-700 boats, so the cache is what makes a re-run cheap and an interrupted
    run resumable without re-fetching what it already has.

    Some boats have no detail page at all: 2018's refs 1333, 1388, 1418 and
    1552 return the site's chrome and nothing else. That is the source, not a
    parse failure - the page holds no boat data in any shape. But a server
    hiccup serving that same shell would look identical and come back 200, so
    an empty result is cached with a marker rather than as fact, and
    --refetch-empty retries just those without discarding the real ones.
    """
    path = CACHE_DIR / str(year) / f"{boatref}.json"
    if path.exists():
        try:
            got = json.loads(path.read_text(encoding="utf-8"))
            if not (got.get("empty") and refetch_empty):
                return got
        except Exception:
            pass
    if cache_only:
        return {}
    html = fetch({"section": "racing", "page": f"boatdetails{year}", "boatref": boatref})
    info = {}
    for tr in BeautifulSoup(html, "html.parser").find_all("tr"):
        cs = [clean(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
        if len(cs) == 2 and cs[0]:
            info.setdefault(cs[0], cs[1])
    out = {
        "sail_no": info.get("Sail number"),
        "boat_type": info.get("Design type"),
        # 2018 calls it Handicap, 2009 calls it TCC; same number.
        "tcc": info.get("TCC") or info.get("Handicap"),
        "entered_by": info.get("Entered by"),
        "skipper": info.get("Skipper"),
        "days_entered": info.get("Days entered"),
    }
    if not any(out.values()):
        out["empty"] = True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    time.sleep(delay)
    return out


def day_numbers(headers):
    """Map race column headers to race numbers, or None if they don't map.

    Returns a list the same length as headers. Weekday headers become their
    position in the Saturday-to-Saturday week (a repeated Sat being day 8);
    R-numbered headers keep their own number.
    """
    if all(RACE_NUMBERED.match(h or "") for h in headers) and headers:
        return [("R", int(RACE_NUMBERED.match(h).group(1))) for h in headers]
    out, used = [], set()
    for h in headers:
        key = (h or "")[:3].title()
        n = None
        for i, w in enumerate(WEEK, start=1):
            if w == key and i not in used:
                n = i
                break
        if n is None:
            out.append(None)
        else:
            used.add(n)
            out.append(("Day", n))
    return out


def status_of(code):
    if code in STARTED_DISQ:
        return "disqualified"
    if code in STARTED_RETIRED:
        return "retired"
    return "finished"


def points_of(value):
    m = re.match(r"^\s*([\d.]+)", value or "")
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def loaded_races(db, year):
    """Race names for this season that already carry entries, for --resume."""
    con = sqlite3.connect(db)
    try:
        rows = con.execute(
            "SELECT ra.race_name FROM races ra "
            "JOIN events e ON e.id = ra.event_id "
            "JOIN regattas r ON r.id = e.regatta_id "
            "WHERE r.name = ? AND e.season_year = ? "
            "AND EXISTS (SELECT 1 FROM race_entries re WHERE re.race_id = ra.id)",
            (REGATTA, year)).fetchall()
    except sqlite3.Error:
        return set()
    finally:
        con.close()
    return {r[0] for r in rows}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("year", type=int)
    p.add_argument("--delay", type=float, default=DELAY)
    p.add_argument("--limit", type=int, default=None, help="first N class series only")
    p.add_argument("--only-class", help="only series whose name contains this")
    p.add_argument("--resume", action="store_true",
                   help="skip races that already have entries loaded")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--cache-only", action="store_true",
                   help="never fetch a boat page; use only what is cached")
    p.add_argument("--refetch-empty", action="store_true",
                   help="retry boat pages previously cached as having no data")
    p.add_argument("--allow-nameless", action="store_true",
                   help="load a season that publishes no sail numbers, instead "
                        "of capturing its rows to data/ for adjudication")
    p.add_argument("--mode", choices=("auto", "races", "series"), default="auto",
                   help="auto decides from whether the season publishes scoring "
                        "codes; see probe_mode")
    args = p.parse_args()

    print(f"Discovering {args.year} series...", flush=True)
    series = discover_series(args.year)
    if args.only_class:
        series = [s for s in series if args.only_class.lower() in s[1].lower()]
    if args.limit:
        series = series[:args.limit]
    if not series:
        print("  no class series published for this season - nothing to do.")
        return
    print(f"  {len(series)} class series", flush=True)

    explicit = args.mode != "auto"
    mode, first_html = args.mode, None
    if not explicit:
        mode, first_html = probe_mode(args.year, series[0][0])
        time.sleep(args.delay)
    # A season with no boat pages has no sail numbers, and its rows are a name
    # and nothing else. 2007 and 2008 are both like that. Those are NOT loaded:
    # LOKI, PANTHERA, ISLAND FLING and SNOW LION are already in this database
    # with sail numbers from the RORC legacy archive, and a name-only record
    # cannot be attached to one of those safely - resolving a boat by name is
    # the exact mistake behind every collision in data/boat_merges.csv. Loading
    # them anyway would mint roughly 1,200 shadow boats that inflate every
    # count and that the Duplicates page could never even propose merging,
    # because it needs a known boat type to corroborate a name match and these
    # have none. So the rows are written out for a person to adjudicate, which
    # keeps the data without asserting the identities.
    nameless = first_html is not None and "boatdetails" not in first_html
    capture = None
    if nameless and not args.allow_nameless:
        path = REPO_ROOT / "data" / f"cowes_names_{args.year}.csv"
        capture = []
        print(f"  this season publishes no boat pages, so no sail numbers: "
              f"capturing rows to {path.relative_to(REPO_ROOT)} instead of "
              f"loading them (--allow-nameless to load anyway)", flush=True)
    print(f"  mode: {mode}" + ("  (scoring codes published, so who sailed which "
                               "race is known)" if mode == "races" else
                               "  (no scoring codes published, so per-race "
                               "attendance is unknowable - loading one entry "
                               "per boat per class)"), flush=True)

    done = loaded_races(args.db, args.year) if args.resume else set()
    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    regatta_id = get_or_create_regatta(cur, REGATTA, "Cowes Combined Clubs")
    event_id = get_or_create_event(cur, regatta_id, args.year)

    codes, per_class, unknown = {}, {}, {}
    n_entries = n_boats = n_noref = n_bare = 0
    silent, nosail = [], []
    classes_seen = set()

    for si, (sid, cls) in enumerate(series, start=1):
        print(f"[{si}/{len(series)}] {cls}", flush=True)
        if first_html is not None and si == 1:
            html, first_html = first_html, None   # probe_mode already fetched it
        else:
            html = fetch({"section": "racing", "page": f"points{args.year}",
                          "resultrequest": sid})
            time.sleep(args.delay)
        if mode == "series" and 'class="status"' in html:
            # The mode was decided from one page. If a later one turns out to
            # carry codes, this season can be loaded properly and should be -
            # say so rather than quietly loading the coarser shape.
            print("    !! this page HAS scoring codes though the season was "
                  "read as having none - re-run with --mode races", flush=True)
        headers, rows = parse_standings(html)
        if not rows:
            print("    no standings table - skipping", flush=True)
            continue
        nums = day_numbers(headers)
        classes_seen.add(cls)
        for boatref, name, owner, pos, total, cells in rows:
            if capture is not None:
                capture.append((cls, "" if pos is None else int(pos), name,
                                owner or "", "" if total is None else total))
                continue
            det = boat_details(args.year, boatref, args.delay, args.cache_only,
                               args.refetch_empty) if boatref else {}
            if not boatref:
                n_noref += 1
            sail = norm_upper(det.get("sail_no"))
            if not sail:
                nosail.append(f"{name} in {cls}")
            elif not re.match(r"^[A-Z]{2,4}\s?-?\d", sail):
                # "1521R" with no country prefix. Real: ROCK LOBSTER is
                # published that way. Loaded as given - the database already
                # holds records like 6212L and 1667R - but counted, because a
                # prefixless number is weak identity and may need a merge.
                n_bare += 1
            n_boats += 1
            boat_id = get_or_create_boat(cur, sail, name, det.get("boat_type"),
                                         points_of(det.get("tcc")))
            if boat_id is None:
                continue
            owner_name = owner or det.get("entered_by")
            owner_id = get_or_create_owner(cur, owner_name)

            if mode == "series":
                # One entry per boat per class: what the source actually
                # supports. The standings prove this boat entered this class
                # this season, and the position and total are real; which
                # individual races it sailed is not recoverable, so nothing
                # here claims to know.
                race_name = f"{cls} - Series"
                if args.resume and race_name in done:
                    continue
                n_entries += 1
                per_class[cls] = per_class.get(cls, 0) + 1
                if args.dry_run:
                    continue
                race_id = create_race(cur, event_id, race_name,
                                      status="confirmed", class_label=cls)
                cur.execute(
                    "INSERT OR REPLACE INTO race_entries "
                    "(race_id, boat_id, class, sail_no_used, boat_name_used, "
                    " boat_type_used, tcc, owner_id, owner_name_used, "
                    " skipper_name_used, status, position, points, comments, source) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (race_id, boat_id, cls, sail, norm_upper(name),
                     norm(det.get("boat_type")), points_of(det.get("tcc")),
                     owner_id, norm_upper(owner_name), norm_upper(det.get("skipper")),
                     "finished", int(pos) if pos else None, total,
                     f"series standing: {'' if pos is None else int(pos)} "
                     f"of {len(rows)}, total {total}".strip(), SOURCE))
                continue

            sailed = 0
            for (hdr, value, code), num in zip(cells, nums):
                if code:
                    codes[code] = codes.get(code, 0) + 1
                if code and code not in STARTED_RETIRED and code not in STARTED_DISQ:
                    if code not in DID_NOT_SAIL:
                        unknown[code] = unknown.get(code, 0) + 1
                    continue            # did not sail this race
                if not value and not code:
                    continue            # empty cell: no race for this boat
                if num is None:
                    unknown[f"column:{hdr}"] = unknown.get(f"column:{hdr}", 0) + 1
                    continue
                kind, n = num
                race_name = f"{cls} - Day {n}" if kind == "Day" else f"{cls} - R{n}"
                # Counted before the resume skip, or a resumed run reports every
                # boat whose races were already loaded as having sailed nothing.
                sailed += 1
                if args.resume and race_name in done:
                    continue
                if args.dry_run:
                    n_entries += 1
                    per_class[cls] = per_class.get(cls, 0) + 1
                    continue
                race_id = create_race(cur, event_id, race_name, race_number=n,
                                      status="confirmed", class_label=cls)
                cur.execute(
                    "INSERT OR REPLACE INTO race_entries "
                    "(race_id, boat_id, class, sail_no_used, boat_name_used, "
                    " boat_type_used, tcc, owner_id, owner_name_used, "
                    " skipper_name_used, status, points, comments, source) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (race_id, boat_id, cls, sail, norm_upper(name),
                     norm(det.get("boat_type")), points_of(det.get("tcc")),
                     owner_id, norm_upper(owner_name), norm_upper(det.get("skipper")),
                     status_of(code), points_of(value),
                     (f"{value} {code}".strip() or None), SOURCE))
                n_entries += 1
                per_class[cls] = per_class.get(cls, 0) + 1
            if sailed == 0:
                silent.append(f"{name} ({sail or 'no sail no'}) in {cls}")
        if not args.dry_run:
            conn.commit()

    if args.dry_run:
        conn.rollback()
    else:
        conn.commit()
    conn.close()

    if capture is not None:
        path = REPO_ROOT / "data" / f"cowes_names_{args.year}.csv"
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["Class", "Pos", "BoatName", "Owner", "Total"])
            w.writerows(capture)
        print(f"\n  wrote {len(capture)} row(s) to {path.relative_to(REPO_ROOT)} - "
              f"names only, NOT loaded. A name with no sail number cannot be "
              f"attached to an existing boat safely; see the comment in main().")

    print(f"\n=== {args.year} ===")
    print(f"  {n_entries} entr{'y' if n_entries == 1 else 'ies'} from "
          f"{n_boats} boat-row(s) across {len(classes_seen)} class(es)"
          f"{'  (dry run - nothing written)' if args.dry_run else ''}")
    for cls in sorted(per_class):
        print(f"    {cls:<28} {per_class[cls]:>5}")
    if codes:
        print("  scoring codes seen: " +
              ", ".join(f"{k}={v}" for k, v in sorted(codes.items())))
    print(f"  cells not loaded as entries because the boat did not sail: " +
          ", ".join(f"{k}={codes[k]}" for k in sorted(DID_NOT_SAIL) if k in codes))
    if unknown:
        print("  !! UNRECOGNISED, treated as 'did not sail' and NOT loaded - "
              "check these before trusting the counts:")
        for k, v in sorted(unknown.items()):
            print(f"       {k} x{v}")
    if nosail or n_bare or n_noref:
        print(f"  identity: {len(nosail)} boat(s) with no sail number on file, "
              f"{n_bare} with no country prefix, {n_noref} row(s) with no boat link")
        # Named, not just counted: these are the only rows here that rest on
        # a name rather than a sail number, so they are the only ones that
        # could ever be the wrong boat. get_or_create_boat will not attach
        # them to a record that HAS a sail number, so nothing already
        # identified can be corrupted - but they still want a human eye.
        for s_ in nosail[:10]:
            print(f"       {s_}")
        if len(nosail) > 10:
            print(f"       ... and {len(nosail)-10} more")
    if silent:
        print(f"  {len(silent)} boat(s) in the standings sailed no race we count "
              f"(all DNC/NER/NOD), so they carry no entry:")
        for s in silent[:8]:
            print(f"       {s}")
        if len(silent) > 8:
            print(f"       ... and {len(silent)-8} more")


if __name__ == "__main__":
    main()
