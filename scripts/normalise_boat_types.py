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
import pathlib
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
# Anchored to the END of the name on purpose: a suffix is only a suffix if it
# trails. Matching anywhere ate the standalone letters inside designer
# initialisms, so "S&S 34" grouped under the family key "& 34".
VARIANT_SUFFIX = re.compile(
    r"\s+(ood|o\.?o\.?d|sport|mod|mk\s*[ivx0-9]+|b&c|r[12]|s|e|c|distinction|"
    r"yawl|custom|fc|sq)$", re.I)


# Class/variant acronyms that must stay upper case, or title-casing invents
# false variants ("Sigma 38 OOD" and "Sigma 38 Ood" as two different boats).
ACRONYMS = {"ood": "OOD", "oo": "OOD", "od": "OD", "sq": "SQ", "fc": "FC",
            "b&c": "B&C", "gp": "GP", "tp": "TP", "irc": "IRC", "imoca": "IMOCA",
            "rp": "RP", "hod": "HOD", "sj": "SJ", "mg": "MG", "xp": "Xp",
            "xod": "XOD", "jod": "JOD", "sb": "SB", "rs": "RS", "hp": "HP",
            "dk": "DK", "ims": "IMS", "orc": "ORC", "j": "J",
            # designer initialisms - capitalize() lowercases the second letter
            # and invents a variant ("S&s 34" beside "S&S 34")
            "s&s": "S&S", "c&c": "C&C", "j/v": "J/V", "j&j": "J&J",
            "a&r": "A&R", "n&s": "N&S"}
MK = re.compile(r"^mk\s*([ivx]+|\d+)$", re.I)


# A class acronym jammed onto the model number with no space ("Sigma 38OOD").
# Deliberately narrow: the letters must be a known acronym of 2+ characters, so
# real designations that end in a letter ("J/V 42R") are left alone.
GLUED_ACRONYM = re.compile(r"^(\d+)([A-Za-z]{2,4})$")


def titlecase_model(s):
    """Title-case words, but leave model numbers, roman numerals and known
    class acronyms alone."""
    out = []
    for w in s.split():
        m = GLUED_ACRONYM.match(w)
        if m and m.group(2).lower() in ACRONYMS:
            out.append(m.group(1))
            out.append(ACRONYMS[m.group(2).lower()])
            continue
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


def load_verdicts(path):
    """Read the human calls on which spellings are the same boat.

    This file is the record of decisions a person made looking at the fleet,
    and it is applied on EVERY run. Without that, each week's scrape re-imports
    the raw spellings and quietly undoes the lot.

    Returns (merge_map, drop_set, settled, conflicts):
      merge_map  spelling -> the spelling it should become
      drop_set   spellings that are not boat types at all -> cleared to NULL
      settled    every spelling already ruled on, merged or not. "Keep
                 separate" is a decision too, so those families must stop
                 appearing in the report or it asks the same question forever.
      conflicts  rows that contradict themselves, reported and skipped
    """
    merge, drop, settled, conflicts = {}, set(), set(), []
    path = pathlib.Path(path)
    if not path.exists():
        return merge, drop, settled, [("(missing)", f"no verdict file at {path}")]

    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            fam = (row.get("Family") or "").strip()
            verdict = (row.get("Verdict") or "").strip().lower()
            # Verdicts record the spellings as they read at the time. The
            # spelling rules above keep improving, so run every name in the file
            # through them too - otherwise fixing a rule silently detaches the
            # decision from the boats it was made about.
            def canon(s):
                return canonical(s.strip())[0] if s and s.strip() else ""
            variants = [canon(v) for v in (row.get("Variants") or "").split("|") if v.strip()]
            keep = canon(row.get("KeepAs") or "")
            hold = {canon(v) for v in (row.get("DoNotFold") or "").split("|") if v.strip()}

            if verdict in ("", "?"):
                continue                       # still waiting on a person
            if verdict == "keep separate":
                settled.update(variants)       # decided: leave the spellings alone
                continue
            if verdict == "not a type":
                drop.update(variants)
                settled.update(variants)
                continue
            if verdict != "same boat":
                conflicts.append((fam, f"unrecognised verdict {verdict!r}"))
                continue

            # A merge needs a surviving name, and that name cannot also be
            # held out of its own merge.
            if not keep:
                conflicts.append((fam, "Same boat with no KeepAs name"))
                continue
            if keep in hold:
                conflicts.append((fam, f"KeepAs {keep!r} is also in DoNotFold - "
                                       "cannot both survive and stay separate"))
                continue
            folding = [v for v in variants if v not in hold]
            if not folding:
                conflicts.append((fam, "every spelling held out of the merge"))
                continue
            for v in folding:
                if v != keep:
                    merge[v] = keep
            settled.update(variants)
            settled.add(keep)
    return merge, drop, settled, conflicts


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--report", default="exports/boat_type_uncertainties.csv")
    p.add_argument("--verdicts", default="data/boat_type_verdicts.csv")
    args = p.parse_args()
    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    rows = cur.execute(
        "SELECT id, boat_type FROM boats WHERE boat_type IS NOT NULL AND TRIM(boat_type) != ''"
    ).fetchall()

    merge_map, drop_set, settled, conflicts = load_verdicts(args.verdicts)

    changed = defaultdict(int)
    n = n_merged = n_dropped = 0
    canon_counts = defaultdict(int)
    decided = settled | set(merge_map) | set(merge_map.values()) | drop_set
    for bid, raw in rows:
        new, _ = canonical(raw)
        # The human verdicts are applied on top of the spelling rules, never
        # the other way round - a person looking at the fleet outranks a regex.
        if new in drop_set:
            n_dropped += 1
            if not args.dry_run:
                cur.execute("UPDATE boats SET boat_type = NULL WHERE id = ?", (bid,))
            continue
        if new in merge_map:
            n_merged += 1
            new = merge_map[new]
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

    # Families already ruled on are settled - keep them out of the report so it
    # only ever holds questions still waiting on a person.
    uncertain = [(b, v) for b, v in families.items()
                 if len(v) > 1 and not all(nm in decided for nm, _ in v)]
    uncertain.sort(key=lambda x: -sum(c for _, c in x[1]))

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
    print(f"verdicts from {args.verdicts}: {n_merged} boat(s) folded into an agreed "
          f"spelling, {n_dropped} cleared to no type "
          f"({len(merge_map)} merge rule(s), {len(drop_set)} placeholder(s)).")
    if conflicts:
        print(f"\n  {len(conflicts)} verdict row(s) SKIPPED - they contradict themselves:")
        for fam, why in conflicts:
            print(f"    {fam}: {why}")
    print(f"\n{len(uncertain)} families need a human call - written to {out}")
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
