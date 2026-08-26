#!/usr/bin/env python3
"""
Load Warsash Spring Series / Championships results for the years published as
PDFs (2022-2024) rather than Sailwave HTML (2025-2026, see scrape_warsash.py).

These PDFs are Sailwave "series standings" exports: one row per boat, with a
per-race points column per race sailed. So unlike the HTML years - where we
load each race individually - a PDF year yields ONE entrant record per boat
per class, loaded as a single "Series standings" race. That is not a race
result, and is named so it can be told apart from real per-race rows.

Text layout per class section (note the column order CHANGED between seasons):
    2022:  Rank Type SailNo Boat Rating          Sun 1 Sun 2 ... Total Nett
    2024:  Rank SailNo Boat Class Helm Club Rating  Day 2 Race 1 ... Total Nett

The PDFs have no ruling lines, so pdfplumber's table extraction collapses each
row into a single cell. Rows are therefore reconstructed from word x-positions:
the header line ("Rank ... Rating") gives each column's left edge, and every
word is assigned to the rightmost column starting at or before it. That copes
with the between-season column re-ordering without hard-coding either layout,
and lets a wrapped row - a sail number or boat name spilling onto a second
line, e.g. "GBR114-" + "FE28R" -> "GBR114-FE28R" - be stitched back together.

Any ranked row that still yields no sail number is reported as UNPARSED rather
than guessed at: a truncated sail number would invent a phantom boat, which is
worse than a visible gap.

Usage:
  python3 load_warsash_pdfs.py <db.sqlite> --year 2022 [--delay 3] [--dry-run]
"""
import sys
import re
import csv
import time
import argparse
import subprocess
import tempfile

import requests
import pdfplumber

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0 Safari/537.36 "
                  "(compatible; MarketshareResearchBot/1.0)",
    "Accept": "application/pdf,*/*",
    "Accept-Language": "en-GB,en;q=0.9",
}
UPLOADS = "https://warsashsc.org.uk/wp-content/uploads"

# Sailwave PDF exports per season. 2024's are not linked from the season
# archive page any more; these URLs came from the IRC Solent Report workbook.
YEAR_PDFS = {
    2022: [
        (f"{UPLOADS}/Series-IRC1-5.pdf", "Warsash Spring Series"),
        (f"{UPLOADS}/Series-IRC2-5.pdf", "Warsash Spring Series"),
        (f"{UPLOADS}/Series-IRC3-5.pdf", "Warsash Spring Series"),
        (f"{UPLOADS}/Champs-Overall-IRC1-3.pdf", "Warsash Spring Championships"),
        (f"{UPLOADS}/Champs-Overall-IRC2-2.pdf", "Warsash Spring Championships"),
        (f"{UPLOADS}/Champs-Overall-IRC3-3.pdf", "Warsash Spring Championships"),
    ],
    2023: [
        (f"{UPLOADS}/BG-Champs-Summary-250423.pdf", "Warsash Spring Championships"),
    ],
    2024: [
        (f"{UPLOADS}/WSS-24-BG-Series-Summary-280424-1545.pdf", "Warsash Spring Series"),
        (f"{UPLOADS}/WSS-24-BG-Champs-Summary-290424-1150.pdf", "Warsash Spring Championships"),
    ],
}

SCRIPT_DIR = __import__("pathlib").Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

# "1st Cape 31 GBR315X Jiraffe 1.134 2.0 ..." ->
#   rank, type, sailno, boat, rating
ROW_RE = re.compile(
    r"^\s*(\d+)(?:st|nd|rd|th)\s+"      # rank
    r"(.+?)\s+"                          # boat type (non-greedy)
    # sail number - allows hyphenated forms like GBR624-F (Nordic Folkboats)
    r"([A-Z]{2,3}\d+(?:-?[A-Z0-9]+)*|\d{3,}[A-Z]*)\s+"
    r"(.+?)\s+"                          # boat name (non-greedy)
    r"([01]\.\d{2,3})\b"                 # IRC rating / TCC
)
# "IRC 1 Series (Fleet)" / "IRC 2 Champs" / "IRC 3 Championship (Fleet)"
SECTION_RE = re.compile(r"^\s*(IRC\s*\d+)\b(.*)$", re.I)


def fetch_pdf(url, attempts=3):
    last = None
    for n in range(attempts):
        try:
            r = requests.get(url, timeout=45, headers=BROWSER_HEADERS)
            r.raise_for_status()
            return r.content
        except Exception as e:
            last = e
            if n < attempts - 1:
                time.sleep(3 * (2 ** n))
    raise last


def norm_sailno(raw):
    s = (raw or "").replace(" ", "").upper().strip()
    if s and s[0].isdigit():
        return "GBR" + s
    return s


# Series PDFs rank as "1st/2nd/3rd", the Champs ones as plain "1/2/3".
RANK_RE = re.compile(r"^\d+(?:st|nd|rd|th)?$")
# A plausible sail number: optional country prefix then digits, e.g. GBR5811R,
# BER11, GBR114-FE28R, 2591C. Used to tell a real boat row apart from a
# wrapped continuation line that merely starts with a number.
SAILNO_RE = re.compile(r"^[A-Z]{2,3}\d[A-Z0-9\-]*$")
# Sailwave's trailing legend / branding, which must never be glued onto the
# last data row as if it were wrapped text.
FOOTER_RE = re.compile(
    r"sailwave|scoring software|code\s+description|did not compete|"
    r"disqualification|\bretired\b", re.I)
# Wrapped header sub-lines: race-number and race-date rows under the main
# header ("1 2 1 2 1 2", "14 April 14 April ...", "2024 2024 ..."). Not data.
HEADER_CONT_RE = re.compile(
    r"(?:\d+|-|Jan\w*|Feb\w*|Mar\w*|Apr\w*|May|Jun\w*|Jul\w*|Aug\w*|Sep\w*|Oct\w*|Nov\w*|Dec\w*|\s)+",
    re.I)
# header labels we care about -> our field name. Warsash changed column order
# between seasons (2022: Rank Type SailNo Boat Rating; 2024: Rank SailNo Boat
# Class Helm Club Rating), so columns are located by their header text rather
# than assumed to be in a fixed order.
COL_ALIASES = {
    "rank": "rank", "sailno": "sail_no", "sail": "sail_no",
    "boat": "boat", "class": "boat_type", "type": "boat_type",
    "helm": "helm", "club": "club", "rating": "rating",
}


def _lines_from_page(page):
    """Group a page's words into visual lines, each sorted left-to-right."""
    lines = {}
    for w in page.extract_words():
        lines.setdefault(round(w["top"] / 3) * 3, []).append(w)
    return [sorted(lines[k], key=lambda x: x["x0"]) for k in sorted(lines)]


def _header_columns(words):
    """If this line is a results header, return [(x0, field), ...] else None."""
    texts = [w["text"].strip().lower().rstrip(",") for w in words]
    if "rank" not in texts or "rating" not in texts:
        return None
    cols = []
    for w, t in zip(words, texts):
        if t in COL_ALIASES:
            field = COL_ALIASES[t]
            if all(f != field for _, f in cols):   # first occurrence wins
                cols.append((w["x0"], field))
    cols.sort()
    return cols if len(cols) >= 3 else None


def _row_from_words(words, cols):
    """Assign each word to the rightmost column starting at or before it."""
    out = {f: [] for _, f in cols}
    for w in words:
        field = None
        for x0, f in cols:
            if w["x0"] >= x0 - 3:
                field = f
            else:
                break
        # words beyond the last named column are race scores - ignored
        if field is not None and w["x0"] <= cols[-1][0] + 40:
            out[field].append(w["text"])
    return {f: " ".join(v).strip() for f, v in out.items()}


def parse_pdf(content):
    """-> [{class_label, rows:[{sail_no, boat, boat_type, rating, rank}]}]

    Parsed by column x-position rather than by regex: it survives the column
    re-ordering between seasons, and lets a wrapped row (a sail number or boat
    name spilling onto a second line) be stitched back onto the row above it.
    """
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        with pdfplumber.open(tmp_path) as pdf:
            pages = [_lines_from_page(pg) for pg in pdf.pages]
    finally:
        try:
            __import__("os").unlink(tmp_path)
        except OSError:
            pass

    sections, current, cols, last_row, last_top = [], None, None, None, 0
    for page_lines in pages:
        for words in page_lines:
            text = " ".join(w["text"] for w in words).strip()

            sm = SECTION_RE.match(text)
            if sm and "sailed:" not in text.lower() and not RANK_RE.match(words[0]["text"]):
                label = re.sub(r"\s+", " ", sm.group(1)).upper()
                label = re.sub(r"IRC\s*", "IRC ", label).strip()
                current = {"class_label": label, "rows": [], "unmatched": []}
                sections.append(current)
                cols = last_row = None
                last_top = 0
                continue
            if current is None:
                continue

            hdr = _header_columns(words)
            if hdr:
                cols = hdr
                last_row = None
                continue
            if not cols:
                continue
            # Sailwave stamps a scoring-codes legend and a "SAILWAVE" footer
            # under the last table. Those sit close enough to the final row to
            # pass the proximity check, and being appended as a continuation
            # corrupted its sail number (GBR8752T -> GBR8752TSAILWAVE), which
            # would invent a phantom boat. Treat them as end-of-table.
            if FOOTER_RE.search(text):
                last_row = None
                continue

            row = _row_from_words(words, cols) if RANK_RE.match(words[0]["text"]) else None
            # A line only starts a new boat if it has a rank AND a plausible
            # sail number. Without the sail-number test, wrapped continuation
            # lines that happen to begin with a number ("3600 British" from a
            # wrapped "Sun Fast 3600") would be mistaken for failed rows and
            # their text dropped, silently truncating the boat type.
            if row is not None and SAILNO_RE.match(norm_sailno(row.get("sail_no", ""))):
                sail = norm_sailno(row["sail_no"])
                # The rating column butts up against the first race-score
                # column, so it can capture "1.134 2.0" - pull the TCC out
                # rather than requiring the cell to be exactly the number.
                rm = re.search(r"\b[01]\.\d{2,3}\b", row.get("rating", ""))
                last_row = {
                    "rank": re.sub(r"\D", "", row.get("rank", "")),
                    "boat_type": row.get("boat_type", ""),
                    "sail_no": sail,
                    "boat": row.get("boat", ""),
                    "rating": rm.group(0) if rm else "",
                    "helm": row.get("helm", ""),
                }
                current["rows"].append(last_row)
                last_top = min(w["top"] for w in words)
            elif last_row is not None and min(w["top"] for w in words) - last_top < 22:
                # continuation line: e.g. sail "GBR114-" + "FE28R", or a helm
                # name/boat type that wrapped. Re-assign by column and append.
                cont = _row_from_words(words, cols)
                if cont.get("sail_no"):
                    last_row["sail_no"] = norm_sailno(last_row["sail_no"] + cont["sail_no"])
                for f in ("boat", "boat_type", "helm"):
                    if cont.get(f):
                        last_row[f] = (last_row.get(f, "") + " " + cont[f]).strip()
            elif row is not None and not HEADER_CONT_RE.fullmatch(text):
                # ranked-looking, but no usable sail number and nothing to
                # attach to. Header sub-lines ("14 April 14 April ...",
                # "2024 2024 ...") are excluded - they aren't lost data.
                current["unmatched"].append(text)

    for s in sections:
        for r in s["rows"]:
            r["boat_type"] = re.sub(r"\s+", " ", r["boat_type"]).strip()
            r["boat"] = re.sub(r"\s+", " ", r["boat"]).strip()
    return [s for s in sections if s["rows"]]


def write_csv(rows, out_path, comment):
    cols = ["Position", "Points", "SailNo", "Boat", "BoatType", "Owner",
            "SailedBy", "FinishTime", "Elapsed", "Handicap", "Corrected", "Comments"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        f.write(f"# {comment}\n")
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({
                "Position": r["rank"], "Points": "", "SailNo": r["sail_no"],
                "Boat": r["boat"], "BoatType": r["boat_type"], "Owner": "",
                # Helm, where the season's PDF has that column - recorded as
                # skipper, not owner (see scrape_warsash.py for why).
                "SailedBy": r.get("helm", ""), "FinishTime": "", "Elapsed": "",
                "Handicap": r["rating"], "Corrected": "", "Comments": "",
            })


def load_into_db(db, csv_path, regatta, year, race_name, class_label, source_url):
    cmd = [
        sys.executable, str(SCRIPT_DIR / "load_rorc_csv.py"), db, str(csv_path),
        "--regatta", regatta, "--year", str(year), "--race-name", race_name,
        "--source-url", source_url, "--source", "scrape:warsash-pdf", "--category", "Club",
    ]
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
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--delay", type=float, default=3)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    pdfs = YEAR_PDFS.get(args.year)
    if not pdfs:
        print(f"No PDF list configured for {args.year}. "
              f"(2025-2026 are Sailwave HTML - use scrape_warsash.py.)")
        return

    exports_dir = REPO_ROOT / "exports"
    exports_dir.mkdir(exist_ok=True)

    for i, (url, regatta) in enumerate(pdfs):
        if i > 0:
            time.sleep(args.delay)
        name = url.rsplit("/", 1)[-1]
        print(f"[{i+1}/{len(pdfs)}] {name}  -> {regatta}")
        try:
            sections = parse_pdf(fetch_pdf(url))
        except Exception as e:
            print(f"  FETCH/PARSE FAILED: {type(e).__name__}: {e}")
            continue
        if not sections:
            print("  no parseable class sections found - skipping")
            continue
        for sec in sections:
            rows = sec["rows"]
            race_name = f"{regatta} {args.year} - {sec['class_label']} Series standings"
            safe = re.sub(r"[^\w\- ]", "_", f"{name[:-4]}_{sec['class_label']}")[:120]
            out_path = exports_dir / f"warsash_{safe}.csv"
            write_csv(rows, out_path, f"{race_name} - {len(rows)} boats - {url}")
            print(f"  {sec['class_label']} - {len(rows)} boats -> {out_path.name}")
            for u in sec.get("unmatched", []):
                print(f"    !! UNPARSED ROW (wrapped sail no?) - not loaded: {u[:90]}")
            if not args.dry_run:
                load_into_db(args.db, out_path, regatta, args.year, race_name,
                             sec["class_label"], url)

    print("Done.")


if __name__ == "__main__":
    main()
