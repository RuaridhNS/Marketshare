#!/usr/bin/env python3
"""
Collapse regattas that are really one event, and move class out of the name.

Two faults, both from scrapers naming a regatta after whatever heading the
source page happened to show:

1. THE SAME EVENT UNDER TWO NAMES. "RORC Myth of Malham" and "RORC Myth of
   Malham Race" are one race; so are "Roschier Baltic Sea Race -" and "RORC
   Roschier Baltic Sea Race". They appear side by side in the dashboard as if
   they were unrelated events.

2. CLASS BAKED INTO THE REGATTA NAME. "Vice Admiral's Cup - J/109", "... -
   SB20", "... - Fast 40+" are one regatta with several classes, not eleven
   regattas. The class belongs in the class layer, which is what the
   Series > Regatta > Class > Race hierarchy is for. Baked into the name it
   cannot be filtered, compared or counted.

The suffix is only treated as a class when it LOOKS like one. That guard
matters: "Sat 9th - Sat 16th June 2018" is a date range and
"North Sea Race - ORC 3 - SHORT COURSE" carries a course as well as a class,
so a blind split on " - " would invent classes out of both.

Class is written to the race row and to any entry that has none, never over
an entry that already states its own class.

Idempotent. Run --dry-run first.

Usage:
  python3 consolidate_regattas.py <db.sqlite> [--dry-run]
"""
import re
import argparse
import sqlite3
from collections import defaultdict

# A suffix is a class only if it reads like one. Anything else stays in the name.
CLASS_SUFFIX = re.compile(
    r"^(irc\b.*|orc\b.*|mocra|class\s*40|multihull|"
    r"j/?\d{2,3}|sb\s?20|cape\s*31|fast\s*40\+?|hp\s*30|gp\s*zero|"
    r"quarter\s*ton(ner)?|performance\s*40|diam\s*24(od)?|"
    r"superyacht|spirit of tradition|darings?|finrating.*|"
    r"two[- ]?handed|2h|double[- ]?handed)$", re.I)

# Course qualifiers ride along with a class and are not classes themselves.
COURSE = re.compile(r"^(short|long)\s+course$", re.I)

NOISE_TAIL = re.compile(r"\s*[-–]\s*$")
PREFIX = re.compile(r"^(rorc|rolex|the)\s+", re.I)
# Only a trailing "Race" is noise: "Myth of Malham" and "Myth of Malham Race"
# are one event. Series, Championships, Cup and Trophy DISTINGUISH events -
# stripping those merged Warsash Spring Series into Warsash Spring
# Championships, which are two different regattas.
GENERIC = re.compile(r"\s+race$", re.I)


def split_name(name):
    """-> (base regatta name, class or None). Never splits a non-class suffix."""
    n = NOISE_TAIL.sub("", (name or "").strip())
    parts = [p.strip() for p in re.split(r"\s+[-–]\s+", n)]
    if len(parts) < 2:
        return n, None
    base, tail = parts[0], parts[1:]
    course = [t for t in tail if COURSE.match(t)]
    cls = [t for t in tail if CLASS_SUFFIX.match(t)]
    if not cls:
        return n, None                    # not a class - leave the name alone
    label = cls[0] + (f" ({course[0]})" if course else "")
    return base, label


def merge_key(name):
    """Names that mean the same event collapse to the same key."""
    k = PREFIX.sub("", (name or "").lower())
    k = GENERIC.sub("", k)
    return re.sub(r"[^a-z0-9]", "", k)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    regattas = cur.execute(
        "SELECT id, name, category, region FROM regattas ORDER BY id").fetchall()

    # base name -> the class each regatta id contributes
    cls_of, base_of = {}, {}
    for rid, name, cat, reg in regattas:
        base, cls = split_name(name)
        base_of[rid] = base
        cls_of[rid] = cls

    groups = defaultdict(list)
    for rid, name, cat, reg in regattas:
        groups[(merge_key(base_of[rid]), cat)].append((rid, name, cat, reg))

    n_merged = n_regattas = n_class_written = 0
    log = []
    for (_, cat), members in sorted(groups.items()):
        if len(members) == 1 and cls_of[members[0][0]] is None:
            continue                        # nothing to do

        # survivor: the longest base name, which is the most complete spelling
        members.sort(key=lambda m: (-len(base_of[m[0]]), m[0]))
        keep_id, keep_name, _, keep_reg = members[0]
        canon = base_of[keep_id]
        # regattas.name is UNIQUE, so renaming onto a name that already exists
        # fails. When the canonical name is already taken, that regatta IS the
        # survivor and everything folds into it instead.
        existing = cur.execute("SELECT id FROM regattas WHERE name = ?", (canon,)).fetchone()
        if existing and existing[0] != keep_id:
            keep_id = existing[0]
            log.append(f"  keep   [{keep_id}] {canon!r} (already canonical)")
        elif canon != keep_name:
            if not args.dry_run:
                cur.execute("UPDATE regattas SET name = ? WHERE id = ?", (canon, keep_id))
            log.append(f"  rename [{members[0][0]}] {keep_name!r} -> {canon!r}")

        for rid, name, _, _ in members:
            cls = cls_of[rid]
            # push the baked-in class down onto races and entries
            if cls:
                for (race_id,) in cur.execute(
                        "SELECT r.id FROM races r JOIN events e ON e.id = r.event_id "
                        "WHERE e.regatta_id = ?", (rid,)).fetchall():
                    # There is no class column on races - class_label is only a
                    # key for create_race. The class layer lives on the entries.
                    if not args.dry_run:
                        cur.execute(
                            "UPDATE race_entries SET class = ? "
                            "WHERE race_id = ? AND (class IS NULL OR TRIM(class) = '')",
                            (cls, race_id))
                    n_class_written += 1
            if rid == keep_id:
                continue
            # fold this regatta's seasons into the survivor
            for ev_id, year in cur.execute(
                    "SELECT id, season_year FROM events WHERE regatta_id = ?", (rid,)).fetchall():
                dest = cur.execute(
                    "SELECT id FROM events WHERE regatta_id = ? AND season_year = ?",
                    (keep_id, year)).fetchone()
                if dest is None:
                    if args.dry_run:
                        continue
                    cur.execute("INSERT INTO events (regatta_id, season_year) VALUES (?, ?)",
                                (keep_id, year))
                    dest_id = cur.lastrowid
                else:
                    dest_id = dest[0]
                if not args.dry_run:
                    cur.execute("UPDATE races SET event_id = ? WHERE event_id = ?",
                                (dest_id, ev_id))
                    for cc_id, label, src in cur.execute(
                            "SELECT id, class_label, source FROM event_class_counts "
                            "WHERE event_id = ?", (ev_id,)).fetchall():
                        clash = cur.execute(
                            "SELECT id FROM event_class_counts WHERE event_id = ? "
                            "AND class_label = ? AND source = ?",
                            (dest_id, label, src)).fetchone()
                        if clash:
                            cur.execute("DELETE FROM event_class_counts WHERE id = ?", (cc_id,))
                        else:
                            cur.execute("UPDATE event_class_counts SET event_id = ? WHERE id = ?",
                                        (dest_id, cc_id))
                    cur.execute("DELETE FROM events WHERE id = ?", (ev_id,))
            if not args.dry_run:
                cur.execute("DELETE FROM regattas WHERE id = ?", (rid,))
            log.append(f"  fold   [{rid}] {name!r}" + (f"  class -> {cls!r}" if cls else ""))
            n_merged += 1
        n_regattas += 1

    for line in log:
        print(line)
    print(f"\n{n_merged} regatta(s) folded into {n_regattas} canonical event(s); "
          f"class written to {n_class_written} race(s).")
    left = cur.execute("SELECT COUNT(*) FROM regattas").fetchone()[0]
    print(f"regattas now: {left}")

    if args.dry_run:
        conn.rollback()
        print("\n(dry run - nothing written)")
    else:
        conn.commit()
        print("committed")
    conn.close()


if __name__ == "__main__":
    main()
