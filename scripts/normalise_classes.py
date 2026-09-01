#!/usr/bin/env python3
"""
Canonicalise class labels so one class is one class.

Sources spell the same division differently and every one of them is loaded
verbatim: the RORC season-points tables write "IRC Two", the per-race pages
write "IRC 2". Both are RORC, both are the same class, and both end up in the
database side by side.

The damage is not cosmetic. Races are keyed on (event, race name, class), so
two spellings mint TWO race rows for one race - and a boat entered once then
reads as having sailed twice. DIABLO's 2024 De Guingand Bowl showed four races
for one race sailed in two classes, because each class was recorded under two
spellings.

WHAT IS AND IS NOT DECIDED HERE. Whether two labels mean the same class is not
a judgement - "IRC 2" and "IRC Two" plainly are. WHICH SPELLING SURVIVES is a
judgement, so it lives in data/class_verdicts.csv where it can be edited, and
is re-applied on every run. The file is written on first use with a default
choice per group:

  IRC divisions default to DIGITS ("IRC 2"), even though the word form is more
  common in the data, because digits sort correctly. Alphabetically the words
  give IRC Four, IRC One, IRC Three, IRC Two - which is nobody's idea of class
  order.

  Everything else defaults to whichever spelling has the most entries behind
  it, on the grounds that the majority of sources chose it.

Merging classes turns previously-distinct races into genuine duplicates, so
dedupe_races.py must be run afterwards. This script says so when it finishes.

Usage:
  python3 normalise_classes.py <db.sqlite> [--dry-run] [--verdicts FILE]
"""
import re
import csv
import pathlib
import argparse
import sqlite3
from collections import defaultdict

# Word forms that mean a number. Used only to decide whether two labels are the
# same class, never to rewrite a label.
WORDS = [("zero", "0"), ("two handed", "2h"), ("one", "1"), ("two", "2"),
         ("three", "3"), ("four", "4"), ("five", "5"), ("six", "6"), ("seven", "7")]

IRC_DIVISION = re.compile(r"^IRC\s+(\d|zero|one|two|three|four|five|six|seven)$", re.I)
NUM_WORD = {"zero": "0", "one": "1", "two": "2", "three": "3",
            "four": "4", "five": "5", "six": "6", "seven": "7"}


def same_class_key(label):
    """Two labels sharing this key are the same class spelled differently."""
    k = (label or "").lower().replace("-", " ")
    for w, d in WORDS:
        k = re.sub(r"\b" + w + r"\b", d, k)
    return re.sub(r"[^a-z0-9]", "", k)


def default_keep(variants, counts):
    """The spelling this script would choose, absent a human decision."""
    # IRC divisions: digits, so the classes sort in class order
    digits = [v for v in variants if IRC_DIVISION.match(v) and re.search(r"\d", v)]
    if digits and any(IRC_DIVISION.match(v) for v in variants):
        return sorted(digits, key=lambda v: -counts[v])[0]
    return sorted(variants, key=lambda v: (-counts[v], v))[0]


def load_verdicts(path):
    out = {}
    path = pathlib.Path(path)
    if not path.exists():
        return out
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            keep = (row.get("KeepAs") or "").strip()
            variants = [v.strip() for v in (row.get("Variants") or "").split("|") if v.strip()]
            if not keep or keep.lower() in ("", "?", "leave", "leave apart"):
                continue                      # explicitly left alone
            for v in variants:
                if v != keep:
                    out[v] = keep
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verdicts", default="data/class_verdicts.csv")
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    counts = {cl: n for cl, n in cur.execute(
        "SELECT class, COUNT(*) FROM race_entries WHERE class IS NOT NULL "
        "AND TRIM(class) != '' GROUP BY class")}
    groups = defaultdict(list)
    for cl in counts:
        groups[same_class_key(cl)].append(cl)
    dupes = {k: v for k, v in groups.items() if len(v) > 1}

    # write the decisions file on first use, so the choice is visible and editable
    vpath = pathlib.Path(args.verdicts)
    if not vpath.exists() and dupes:
        vpath.parent.mkdir(parents=True, exist_ok=True)
        with open(vpath, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Group", "Variants", "Entries", "KeepAs"])
            for k, v in sorted(dupes.items(), key=lambda x: -sum(counts[i] for i in x[1])):
                v = sorted(v, key=lambda i: -counts[i])
                w.writerow([v[0], " | ".join(v), sum(counts[i] for i in v),
                            default_keep(v, counts)])
        print(f"wrote {vpath} with a default decision per group - edit KeepAs to override\n")

    mapping = load_verdicts(vpath)
    if not mapping:
        print("no class merges to apply.")
        return

    n_rows = 0
    applied = []
    for old, new in sorted(mapping.items()):
        n = counts.get(old, 0)
        if not n:
            continue
        applied.append((old, new, n))
        if not args.dry_run:
            cur.execute("UPDATE race_entries SET class = ? WHERE class = ?", (new, old))
        n_rows += n

    for old, new, n in sorted(applied, key=lambda x: -x[2]):
        print(f"  {old!r:26} -> {new!r:20} ({n} entries)")

    left = {k: v for k, v in dupes.items()
            if not any(x in mapping for x in v)}
    print(f"\n{len(applied)} label(s) merged, {n_rows} entries relabelled.")
    if left:
        print(f"{len(left)} group(s) deliberately left apart (KeepAs blank in the file):")
        for k, v in list(left.items())[:8]:
            print(f"    {sorted(v)}")

    if args.dry_run:
        conn.rollback()
        print("\n(dry run - nothing written)")
    else:
        conn.commit()
        print("\ncommitted")
        print("NOW RUN dedupe_races.py: merging classes turns what were two "
              "separate race rows into genuine duplicates.")
    conn.close()


if __name__ == "__main__":
    main()
