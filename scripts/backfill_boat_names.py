#!/usr/bin/env python3
"""
Derive each boat's naming history from the names it actually raced under.

race_entries.boat_name_used records the name on the entry list at the time, so
a boat's rename history is already in the data - 782 boats have raced under
more than one name. What is NOT in the data is which of those are real renames.

Most variation is sponsor decoration, not a new name:

    GALAHAD OF COWES  ->  "Galahad Logic", "Spearelectrical Galahad",
                          "Bridges Estate Agents", "Bordier UK Galahad"
    LANCELOT II       ->  "Lancelot Logic", "Windward Assoc. on Lancelot II Logic"

while a genuine rename shares nothing with the old name:

    TSCHUSS (Christian Zugel)  ->  STANDFAST (Simon Patterson)

The rule used here: if two names share a significant word, they are treated as
the same name wearing different sponsors and collapsed to the shortest form. If
they share nothing, it is recorded as a rename. That is a heuristic, so each
segment stores its confidence and the raw variants behind it, and the dashboard
shows sponsor variants as a footnote rather than as separate names.

Also handles the "<sponsor> ON <boat>" pattern, and strips a trailing charter
company where the boat name survives.

Usage:
  python3 backfill_boat_names.py <db.sqlite> [--dry-run]
"""
import re
import difflib
import unicodedata
import argparse
import sqlite3
from collections import defaultdict

SCHEMA = """
CREATE TABLE IF NOT EXISTS boat_name_history (
    id              INTEGER PRIMARY KEY,
    boat_id         INTEGER NOT NULL REFERENCES boats(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,        -- the cleaned name
    first_season    INTEGER,
    last_season     INTEGER,
    variants        TEXT,                 -- raw spellings seen, pipe-separated
    confidence      TEXT,                 -- 'certain' | 'likely'
    UNIQUE(boat_id, name)
);
"""

# words that carry no identity - dropped before comparing two names
NOISE = re.compile(
    r"\b(of|the|on|team|racing|sailing|yacht|yachts|charters?|ltd|limited|llp|"
    r"plc|uk|gbr|group|logic|academy|trust|foundation|challenge|project)\b", re.I)
# "<sponsor> ON <boat>" - the boat is the right-hand side
ON_SPLIT = re.compile(r"^(.*?)\s+\bon\b\s+(.+)$", re.I)


# Telltale of UTF-8 bytes stored as Latin-1 characters: "TSCHUSS" arrives as
# TSCHÃ¼SS, the two bytes of an umlaut read as two separate characters.
MOJIBAKE = re.compile(r"[Â-Ãâ][-¿]")


def demojibake(s):
    """Repair UTF-8-read-as-Latin-1, but only when it demonstrably applies.
    Applying this blindly across the database would WRECK correctly-stored
    accented names, so it runs only on strings carrying the telltale byte pair
    and only if the round-trip actually decodes."""
    if not s or not MOJIBAKE.search(s):
        return s
    try:
        fixed = s.encode("latin-1").decode("utf-8")
        return fixed if fixed != s else s
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def deaccent(s):
    """TSCHUSS and TSCH(u-umlaut)SS are the same boat. Sources disagree on
    whether accents survive their export, so they are folded before comparing."""
    s = demojibake(s)
    return "".join(ch for ch in unicodedata.normalize("NFKD", s or "")
                   if not unicodedata.combining(ch))


def words(name):
    s = NOISE.sub(" ", deaccent(name).upper())
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    out = {w for w in s.split() if len(w) > 2 and not w.isdigit()}
    # also index the de-spaced whole, so "TESSA 3"/"TESSA3" and
    # "SIDNEY II"/"SYDNEYII" cluster together instead of reading as renames
    squashed = re.sub(r"[^A-Z0-9]", "", s)
    if len(squashed) > 3:
        out.add(squashed)
        out.add(re.sub(r"\d+$", "", squashed) or squashed)
    return out


def skeleton(name):
    """ASCII letters/digits only. Some sources have mangled non-ASCII on the way
    in - one boat is stored with a FRACTION SLASH inside it where an umlaut
    should be - so comparisons fall back to what survives that."""
    return re.sub(r"[^A-Z0-9]", "", deaccent(name).upper())


def near(a, b):
    """Close enough to be the same name despite a mangled character or a lost
    space: TSCH?SS/TSCHUSS, TESSA 3/TESSA3. Deliberately tight - 0.82 keeps
    genuinely different names apart."""
    sa, sb = skeleton(a), skeleton(b)
    if not sa or not sb or abs(len(sa) - len(sb)) > 3:
        return False
    return difflib.SequenceMatcher(None, sa, sb).ratio() >= 0.82


def clean(name):
    n = re.sub(r"\s+", " ", demojibake(name or "").strip())
    m = ON_SPLIT.match(n)
    if m and words(m.group(2)):
        n = m.group(2).strip()
    return n


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    cur.executescript(SCHEMA)

    rows = cur.execute("""
        SELECT re.boat_id, e.season_year, re.boat_name_used
        FROM race_entries re
        JOIN races ra ON ra.id = re.race_id
        JOIN events e ON e.id = ra.event_id
        WHERE re.boat_name_used IS NOT NULL AND TRIM(re.boat_name_used) != ''
          AND e.season_year IS NOT NULL
        ORDER BY re.boat_id, e.season_year
    """).fetchall()

    per_boat = defaultdict(list)
    for bid, yr, raw in rows:
        per_boat[bid].append((yr, clean(raw)))

    if not args.dry_run:
        cur.execute("DELETE FROM boat_name_history")

    n_boats = n_seg = n_renamed = 0
    n_overlap = [0]
    examples = []
    for bid, seen in per_boat.items():
        # group the raw names into identity clusters by shared significant words
        clusters = []          # [{'words': set, 'names': {name: [years]}}]
        for yr, nm in seen:
            w = words(nm)
            if not w:
                continue
            hit = next((c for c in clusters if c["words"] & w), None)
            if hit is None:
                hit = next((c for c in clusters
                            if any(near(nm, other) for other in c["names"])), None)
            if hit:
                hit["words"] |= w
                hit["names"].setdefault(nm, []).append(yr)
            else:
                clusters.append({"words": set(w), "names": {nm: [yr]}})

        if not clusters:
            continue
        n_boats += 1
        if len(clusters) > 1:
            n_renamed += 1

        segs = []
        for c in clusters:
            yrs = [y for ys in c["names"].values() for y in ys]
            # the shortest spelling is the one with least sponsor attached
            canonical = min(c["names"], key=lambda s: (len(words(s)), len(s)))
            segs.append((min(yrs), max(yrs), canonical, sorted(c["names"])))
        segs.sort()

        for first, last, name, variants in segs:
            n_seg += 1
            if not args.dry_run:
                cur.execute(
                    "INSERT OR REPLACE INTO boat_name_history "
                    "(boat_id, name, first_season, last_season, variants, confidence) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (bid, name, first, last, " | ".join(variants),
                     "certain" if len(variants) == 1 else "likely"))
        # Segments that overlap in time are not a rename - a boat cannot be
        # called two things at once. It means several different boats were
        # merged into this record, almost always because get_or_create_boat
        # keys on sail number alone. Recorded so the dashboard can flag it
        # rather than presenting a fictional rename chain.
        overlapping = any(segs[i][1] >= segs[i + 1][0] for i in range(len(segs) - 1))
        if overlapping:
            n_overlap[0] += 1
            if not args.dry_run:
                cur.execute("UPDATE boat_name_history SET confidence='overlapping' "
                            "WHERE boat_id = ?", (bid,))
        if len(segs) > 1 and not overlapping and len(examples) < 8:
            examples.append((bid, " -> ".join(f"{n} ({a}-{b})" for a, b, n, _ in segs)))

    if not args.dry_run:
        conn.commit()
    print(f"{n_boats} boat(s) with a naming history; {n_seg} name segment(s) written.")
    print(f"{n_renamed} boat(s) look genuinely renamed (names sharing no significant word).")
    print(f"{n_overlap[0]} of those have OVERLAPPING name periods - a boat cannot be called two")
    print("things at once, so those are probably several boats merged on a shared sail number.")
    print("\nexamples:")
    for bid, line in examples:
        print(f"  [{bid}] {line[:110]}")
    if args.dry_run:
        print("\n(dry run - nothing written)")
    conn.close()


if __name__ == "__main__":
    main()
