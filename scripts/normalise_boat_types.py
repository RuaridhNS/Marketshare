#!/usr/bin/env python3
"""
Canonicalise boat_type to the builder's own published naming.

The same design arrives from every source spelled differently - 1,397 distinct
type strings for 3,184 boats - which split the type filter into fragments that
each matched a different subset of the same fleet.

Conventions below were checked against each builder's own site, not assumed:

  JPK        jpk.fr/en/gamme lists "JPK 1010", "JPK 1080", "JPK 960",
             "JPK 1030", "JPK 1180" - NO decimal point. So "JPK 10.10" is the
             non-standard form, not the other way round.
             Careful: JPK 110 and JPK 998 are separate real models.
  Sun Fast   Jeanneau brands it as two words, "Sun Fast 3300".
             Sun Fast 37 / 35 / 32 are older models and are NOT the same boats
             as 3600 / 3300 / 3200 - they must never be merged.
  X-Yachts   x-yachts.com writes "X-332", "X-362 Sport" with a hyphen. The
             newer performance line is "Xp 44" and keeps its own form.
  First      Beneteau writes title case, "First 40.7".

Everything else gets case and whitespace tidied only. Models are never merged
on similarity: a suffix like OOD, Sport, R2, B&C or Mk II usually marks a
genuinely different boat, so those are reported as UNCERTAIN for a human to
decide rather than silently collapsed.

Usage:
  python3 normalise_boat_types.py <db.sqlite> [--dry-run] [--report FILE.csv]
"""
import re
import csv
import argparse
import sqlite3
from collections import defaultdict

RATING_TAIL = re.compile(r"\s+[0-2]\.\d{1,2}(?:\s|$).*$")
J_BOAT = re.compile(r"^J\s*[/\-_ ]?\s*(\d{2,3})\s*([A-Za-z])?$", re.I)
JPK = re.compile(r"^JPK\s*(\d{1,2})[.,](\d{2})\s*(.*)$", re.I)
JPK_PLAIN = re.compile(r"^JPK\s*(\d{2,4})\s*(.*)$", re.I)
SUNFAST = re.compile(r"^sun\s*fast\s*(.*)$", re.I)
XY = re.compile(r"^X\s*[-–]?\s*(\d{2,3})\s*(.*)$", re.I)
XP = re.compile(r"^Xp\s*[-–]?\s*(\d{2,3})\s*(.*)$", re.I)
FIRST = re.compile(r"^first\s+(.*)$", re.I)

# Suffixes that usually denote a genuinely different boat - flagged, not merged.
VARIANT_SUFFIX = re.compile(
    r"\b(ood|o\.?o\.?d|sport|mod|mk\s*[ivx0-9]+|b&c|r[12]|s|e|c|distinction|"
    r"yawl|custom|fc|sq)\b", re.I)


# Class/variant acronyms that must stay upper case, or title-casing invents
# false variants ("Sigma 38 OOD" and "Sigma 38 Ood" as two different boats).
ACRONYMS = {"ood": "OOD", "oo": "OOD", "od": "OD", "sq": "SQ", "fc": "FC",
            "b&c": "B&C", "gp": "GP", "tp": "TP", "irc": "IRC", "imoca": "IMOCA",
            "rp": "RP", "hod": "HOD", "sj": "SJ", "mg": "MG", "xp": "Xp",
            "xod": "XOD", "jod": "JOD", "sb": "SB", "rs": "RS", "hp": "HP",
            "dk": "DK", "ims": "IMS", "orc": "ORC", "j": "J"}
MK = re.compile(r"^mk\s*([ivx]+|\d+)$", re.I)


def titlecase_model(s):
    """Title-case words, but leave model numbers, roman numerals and known
    class acronyms alone."""
    out = []
    for w in s.split():
        low = w.lower().strip(".")
        if low in ACRONYMS:
            out.append(ACRONYMS[low])
        elif MK.match(w):
            # normalise Mk2 / MK II / mkII to a single "Mk II" style
            num = MK.match(w).group(1)
            roman = {"1": "I", "2": "II", "3": "III", "4": "IV"}.get(num, num.upper())
            out.append(f"Mk {roman}")
        elif re.search(r"\d", w) or re.fullmatch(r"[IVX]+", w.upper()) and len(w) <= 4:
            out.append(w)
        else:
            out.append(w.capitalize())
    return " ".join(out)


def canonical(raw):
    if not raw:
        return raw, None
    s = re.sub(r"\s+", " ", str(raw)).strip()
    s = RATING_TAIL.sub("", s).strip()
    if not s:
        return raw, None

    m = J_BOAT.match(s)
    if m:
        return f"J/{m.group(1)}{(m.group(2) or '').lower()}", None

    m = JPK.match(s)                       # JPK 10.10 -> JPK 1010
    if m:
        rest = titlecase_model(m.group(3)).strip()
        return f"JPK {m.group(1)}{m.group(2)}" + (f" {rest}" if rest else ""), None
    m = JPK_PLAIN.match(s)
    if m:
        rest = titlecase_model(m.group(2)).strip()
        return f"JPK {m.group(1)}" + (f" {rest}" if rest else ""), None

    m = SUNFAST.match(s)                   # Sunfast 3600 -> Sun Fast 3600
    if m:
        rest = titlecase_model(m.group(1)).strip()
        return f"Sun Fast {rest}".strip(), None

    m = XP.match(s)                        # Xp 44 keeps its own form
    if m:
        rest = titlecase_model(m.group(2)).strip()
        return f"Xp {m.group(1)}" + (f" {rest}" if rest else ""), None
    m = XY.match(s)                        # X 332 / X332 -> X-332
    if m:
        rest = titlecase_model(m.group(2)).strip()
        return f"X-{m.group(1)}" + (f" {rest}" if rest else ""), None

    m = FIRST.match(s)                     # FIRST 40.7 -> First 40.7
    if m:
        return f"First {titlecase_model(m.group(1)).strip()}", None

    return titlecase_model(s), None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--report", default="exports/boat_type_uncertainties.csv")
    args = p.parse_args()
    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    rows = cur.execute(
        "SELECT id, boat_type FROM boats WHERE boat_type IS NOT NULL AND TRIM(boat_type) != ''"
    ).fetchall()

    changed = defaultdict(int)
    n = 0
    canon_counts = defaultdict(int)
    for bid, raw in rows:
        new, _ = canonical(raw)
        canon_counts[new] += 1
        if new and new != raw:
            changed[(raw, new)] += 1
            n += 1
            if not args.dry_run:
                cur.execute("UPDATE boats SET boat_type = ? WHERE id = ?", (new, bid))

    for (old, new), cnt in sorted(changed.items(), key=lambda x: -x[1])[:25]:
        print(f"  {old!r:32} -> {new!r:24} ({cnt})")
    if len(changed) > 25:
        print(f"  ... and {len(changed)-25} more mappings")

    # ---- uncertainties: canonical names that differ only by a variant suffix
    families = defaultdict(list)
    for name, cnt in canon_counts.items():
        base = VARIANT_SUFFIX.sub("", name).strip(" -/")
        base = re.sub(r"\s+", " ", base)
        families[base].append((name, cnt))

    uncertain = [(b, v) for b, v in families.items() if len(v) > 1]
    uncertain.sort(key=lambda x: -sum(c for _, c in x[1]))

    import pathlib
    out = pathlib.Path(args.report)
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Family", "Variant", "Boats", "SameBoat? (y/n)", "Note"])
        for base, variants in uncertain:
            for name, cnt in sorted(variants, key=lambda x: -x[1]):
                w.writerow([base, name, cnt, "", ""])

    print(f"\n{n} boat(s) retyped; {len(canon_counts)} distinct types remain "
          f"(was {len({r[1] for r in rows})}).")
    print(f"{len(uncertain)} families need a human call - written to {out}")
    print("\nTop uncertainties (is each row the same boat as its siblings?):")
    for base, variants in uncertain[:12]:
        vs = ", ".join(f"{nm} ({c})" for nm, c in sorted(variants, key=lambda x: -x[1]))
        print(f"  {base:22} -> {vs}")

    if args.dry_run:
        conn.rollback()
        print("\n(dry run - nothing written)")
    else:
        conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
