#!/usr/bin/env python3
"""Pull every boat row out of the 379 Marketshare workbooks into one staging CSV.

The workbooks are hand-kept, so nothing is assumed: the header row is hunted for
in the first dozen rows, column names are mapped through a synonym table, and a
sheet that turns out to be a dropdown lookup list (the sailmaker picklist) or a
rating table rather than an entry list is skipped.
"""
import csv, pathlib, re, collections, warnings
from openpyxl import load_workbook

warnings.filterwarnings("ignore")
ROOT = pathlib.Path(r"C:\Users\ruari\OneDrive - North Technology Group\Marketshare backups\OneDrive_2026-09-16\Marketshare Folder")
OUT = pathlib.Path("staging_entries.csv")

def norm(s):
    s = re.sub(r"[\x00-\x1f\x7f-\x9f]+", " ", str(s or ""))
    s = s.replace("\u00a0", " ").strip().lower()
    s = re.sub(r"[\[\]\(\):.#/]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

SYN = {
    "sail_no": ["sail number", "sail no", "sail num", "sail", "sail no.",
                "bow number", "bow no", "hull no", "hull number"],
    # superyacht workbooks head the boat column "Team"
    "boat_name": ["yacht name", "boat name", "boat", "yacht", "boat sponsor",
                  "name of boat", "vessel", "boat/sponsor", "team", "team name",
                  "entry"],
    "owner": ["owner s name", "owner name", "owner", "owners name", "owner s"],
    "skipper": ["skipper name", "skipper s name", "skipper", "sailed by",
                "sailor", "helm", "helmsman", "driver"],
    "boat_type": ["yacht type", "boat type", "type", "design", "model"],
    "size": ["size", "loa", "length"],
    "builder": ["shipyard", "builder", "yard"],
    "division": ["class", "division", "fleet", "category"],
    "sailmaker": ["sailmaker", "sail maker", "sail maker drop down list",
                  "sailmaker drop down list", "sails", "sail brand"],
    "sail_material": ["sail material", "sail material dacron for class rules",
                      "material", "cloth"],
    "partial": ["partial"],
    "other_sails": ["other"],
    "masts": ["ntg masts y n", "ntg masts", "masts", "rig and rigging",
              "rig & rigging", "ntg mast"],
    "clothing": ["perf clothing y n", "perf clothing", "performance clothing",
                 "clothing"],
    "sales_lead": ["north sails sales lead", "sales lead", "ns sales lead"],
    "rep": ["north sails rep onboard", "north sails reps onboard",
            "rep onboard", "reps onboard", "north sails rep",
            "other north experts involved and other info",
            "north sails experts onsite"],
    "country": ["country code", "country", "nat", "nationality", "flag"],
    "club": ["club", "yacht club"],
}
LOOKUP = {}
for canon, names in SYN.items():
    for n in names:
        LOOKUP.setdefault(norm(n), canon)

FIELDS = ["src_file", "src_sheet", "folder_year", "event_file", "row_no",
          "sail_no", "boat_name", "owner", "skipper", "boat_type", "size",
          "builder", "division",
          "sailmaker", "sail_material", "partial", "other_sails", "masts",
          "clothing", "sales_lead", "rep", "country", "club"]

# a sheet whose only content is the sailmaker picklist
PICKLIST = {"unknown", "north", "incidence", "technique voile", "doyle",
            "elvstrom", "one sails", "quantum", "other", "all purpose",
            "ullman", "hyde", "sanders", "banks", "evolution"}

def find_header(ws, maxscan=12):
    """-> (row_index, {col_index: canonical}) for the best-scoring header row."""
    best, best_score, best_row = None, 0, None
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=maxscan,
                                         values_only=True), 1):
        m, seen = {}, set()
        for j, c in enumerate(row):
            k = LOOKUP.get(norm(c))
            if k and k not in seen:
                m[j] = k
                seen.add(k)
        score = len(m) + (2 if "boat_name" in seen else 0) + (1 if "sailmaker" in seen else 0)
        if score > best_score:
            best, best_score, best_row = m, score, i
    if best_score < 3:
        return None, None
    return best_row, best

def rows_of(ws, hrow, cmap, cap=4000, blank_stop=80):
    """Some sheets declare a phantom dimension of a million rows, so the scan is
    capped and gives up after a long run of empty ones."""
    out, blanks = [], 0
    last = min(ws.max_row or cap, hrow + cap)
    for i, row in enumerate(ws.iter_rows(min_row=hrow + 1, max_row=last,
                                         values_only=True), hrow + 1):
        if not any(c not in (None, "") for c in row):
            blanks += 1
            if blanks >= blank_stop:
                break
            continue
        blanks = 0
        rec = {}
        for j, canon in cmap.items():
            v = row[j] if j < len(row) else None
            if v is None:
                continue
            v = re.sub(r"[\x00-\x1f\x7f-\x9f]+", " ", str(v)).replace("\u00a0", " ").strip()
            if v and v.lower() not in ("none", "n/a", "-", "--"):
                rec[canon] = v
        if not rec:
            continue
        # a row is an entry only if it names a boat or carries a sail number
        if not (rec.get("boat_name") or rec.get("sail_no")):
            continue
        # skip repeated header rows and "Total:" banners
        joined = norm(" ".join(rec.values()))
        if joined.startswith("total") or "irc overall" in joined:
            continue
        if rec.get("boat_name") and norm(rec["boat_name"]) in LOOKUP:
            continue
        rec["row_no"] = i
        out.append(rec)
    return out

def main():
    files = sorted(ROOT.rglob("*.xlsx"))
    all_rows, skipped, stats = [], [], collections.Counter()
    for f in files:
        rel = f.relative_to(ROOT)
        year = rel.parts[0]
        try:
            wb = load_workbook(f, read_only=True, data_only=True)
        except Exception as ex:
            skipped.append((str(rel), "-", f"open failed: {ex!r}"[:80]))
            continue
        got_any = False
        for ws in wb.worksheets:
            vals = []
            try:
                hrow, cmap = find_header(ws)
            except Exception as ex:
                skipped.append((str(rel), ws.title, f"header scan: {ex!r}"[:60]))
                continue
            if not cmap:
                skipped.append((str(rel), ws.title, "no header found"))
                continue
            try:
                vals = rows_of(ws, hrow, cmap)
            except Exception as ex:
                skipped.append((str(rel), ws.title, f"row read: {ex!r}"[:60]))
                continue
            # picklist guard
            names = [norm(v.get("boat_name", "")) for v in vals]
            if names and sum(1 for n in names if n in PICKLIST) > len(names) * 0.6:
                skipped.append((str(rel), ws.title, "sailmaker picklist"))
                continue
            if not vals:
                skipped.append((str(rel), ws.title, "header but no entries"))
                continue
            for v in vals:
                v["src_file"] = str(rel)
                v["src_sheet"] = ws.title
                v["folder_year"] = year
                v["event_file"] = f.stem
                all_rows.append(v)
            stats[str(rel)] += len(vals)
            got_any = True
        wb.close()
        if not got_any:
            skipped.append((str(rel), "*", "no usable sheet in workbook"))

    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)

    with open("staging_skipped.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "sheet", "reason"])
        w.writerows(skipped)

    print(f"workbooks      : {len(files)}")
    print(f"entry rows     : {len(all_rows):,}")
    print(f"workbooks w/rows: {len(stats)}")
    print(f"skipped sheets : {len(skipped)}")
    print("\nskip reasons:")
    for r, n in collections.Counter(s[2] for s in skipped).most_common():
        print(f"  {n:4d}  {r}")
    filled = collections.Counter()
    for r in all_rows:
        for k in FIELDS:
            if r.get(k):
                filled[k] += 1
    print("\nfield fill:")
    for k in FIELDS:
        if k in ("src_file", "src_sheet", "folder_year", "event_file", "row_no"):
            continue
        print(f"  {filled[k]:6,}  {100*filled[k]/max(len(all_rows),1):5.1f}%  {k}")

if __name__ == "__main__":
    main()
