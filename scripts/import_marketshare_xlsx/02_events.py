#!/usr/bin/env python3
"""Turn the staging rows into an event list: clean name, year, dates, size."""
import csv, re, collections, pathlib, json

MONTH = ("january february march april may june july august september october "
         "november december").split()
MON3 = {m[:3]: i + 1 for i, m in enumerate(MONTH)}

def clean_name(stem):
    s = stem
    s = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️]", "", s)   # emoji markers
    s = re.sub(r"\[[^\]]*\]", " ", s)                                  # [Aug 17 - 24]
    s = re.sub(r"\([^)]*\d[^)]*\)", " ", s)                            # (May 3)
    s = re.sub(r"\bMarketshares?\b", " ", s, flags=re.I)
    s = re.sub(r"\bv\d+\b", " ", s, flags=re.I)
    s = re.sub(r"[-–—_]+\s*$", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -–—,&")
    return s

def dates_from(stem, folder_year):
    """-> (start, end) as ISO where the filename carries them."""
    yr = None
    m = re.search(r"\b(20\d\d)\b", stem)
    if m:
        yr = int(m.group(1))
    if yr is None:
        # "2025", but also legacy folders like "August Marketshares 2024"
        fm = re.search(r"\b(20\d\d)\b", folder_year)
        if fm:
            yr = int(fm.group(1))
    br = re.search(r"\[([^\]]*)\]|\(([^)]*)\)", stem)
    if not br or yr is None:
        return (None, None, yr)
    txt = (br.group(1) or br.group(2) or "").strip()
    toks = re.findall(r"([A-Za-z]{3,9})?\s*(\d{1,2})", txt)
    mons = re.findall(r"([A-Za-z]{3,9})", txt)
    mons = [MON3.get(x[:3].lower()) for x in mons if x[:3].lower() in MON3]
    nums = [int(n) for _, n in toks if n and int(n) <= 31]
    if not mons or not nums:
        return (None, None, yr)
    m1 = mons[0]
    m2 = mons[1] if len(mons) > 1 else m1
    d1 = nums[0]
    d2 = nums[-1] if len(nums) > 1 else d1
    y1, y2 = yr, yr
    if m2 < m1:                      # crosses new year
        y2 = yr + 1
    try:
        s = f"{y1:04d}-{m1:02d}-{min(d1,28 if m1==2 else 31):02d}"
        e = f"{y2:04d}-{m2:02d}-{min(d2,28 if m2==2 else 31):02d}"
    except Exception:
        return (None, None, yr)
    return (s, e, yr)

def sheet_year(name):
    """A sheet named '2025' is its own season: several workbooks keep two
    editions of the same regatta side by side."""
    m = re.fullmatch(r"\s*(20\d\d)(\s+Marketshare)?\s*", name or "")
    return int(m.group(1)) if m else None

rows = list(csv.DictReader(open("staging_entries.csv", encoding="utf-8")))
ev = collections.OrderedDict()
for r in rows:
    sy = sheet_year(r["src_sheet"])
    key = r["src_file"] + ("#%d" % sy if sy else "")
    r["event_key"] = key
    d = ev.setdefault(key, {"src_file": r["src_file"], "stem": r["event_file"],
                            "folder": r["folder_year"], "rows": 0,
                            "sheet_year": sy, "event_key": key,
                            "sheets": set(), "sm": collections.Counter()})
    d["rows"] += 1
    d["sheets"].add(r["src_sheet"])
    if r.get("sailmaker"):
        d["sm"][r["sailmaker"].strip().lower()] += 1

out = []
for k, d in ev.items():
    s, e, yr = dates_from(d["stem"], d["folder"])
    if d["sheet_year"]:                     # the sheet's own year wins
        if yr and yr != d["sheet_year"]:
            s = e = ""
        yr = d["sheet_year"]
    out.append({"event_key": k, "src_file": d["src_file"],
                "event_name": clean_name(d["stem"]),
                "year": yr, "start_date": s or "", "end_date": e or "",
                "entries": d["rows"], "sheets": "|".join(sorted(d["sheets"])),
                "sailmaker_rows": sum(d["sm"].values())})

# stamp the event key back onto the entries so every later stage groups the same way
with open("staging_entries.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
    w.writeheader(); w.writerows(rows)

out.sort(key=lambda r: (-(r["year"] or 0), r["event_name"]))
with open("staging_events.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(out[0].keys()))
    w.writeheader(); w.writerows(out)

print(f"events: {len(out)}   entries: {sum(o['entries'] for o in out):,}")
print(f"with parsed dates: {sum(1 for o in out if o['start_date'])}")
byyear = collections.Counter(o["year"] for o in out)
print("by year:", dict(sorted(byyear.items(), key=lambda x: (x[0] or 0))))
print("\n-- 30 largest --")
for o in sorted(out, key=lambda r: -r["entries"])[:30]:
    print(f"  {o['entries']:5d}  {o['year']}  {o['event_name'][:62]}")
