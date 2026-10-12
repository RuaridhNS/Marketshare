#!/usr/bin/env python3
"""Pull apart the boat records that are several boats.

Boats are matched on sail number, and a bare number is not one. Every boat
that ever raced under "45" anywhere in the world became a single record:
BELLA MENTE the Maxi 72, Bullit the Cape 31, a Redwing called GOSLING and an
Oyster Bay 50069. The record on "2" held twenty-four names and twenty-four
types. A fused record counts ONCE in market share by distinct boat and looks
hyper-active in the analysis, so this is not cosmetic.

Scope is deliberately narrow: only records whose sail number carries no
letters at all - a bare number, or junk like "4.5". Those cannot identify a
boat in any fleet. Records on a CLASS number are left alone, because X174 and
R6 are identities inside their class, and so are national numbers, where
several names usually means a charter hull racing under a new sponsor each
event - GBR6840R is Linklaters Lancelot one week and Rightmove Team Sparrow
the next, and that is one boat, correctly recorded.

HOW A BOAT IS IDENTIFIED HERE. Entries are grouped by the name they were
entered under, then groups whose TYPE agrees are fused back together, because
one boat changes name constantly and a sail number does not: "ARTEMIS FIGARO
21", "ARTEMIS 21" and "THE OFFSHORE ACADEMY 21" are one Figaro. Only a group
whose type PROVES it is a different boat is split off. A group with nothing to
go on stays where it is - being wrong by leaving a record fused is
recoverable, minting a boat that never existed is not.

Three things decide whether the type proves anything:

  * The class column counts as a type. On Solent one-design entries
    boat_type_used is "-" or empty and race_entries.class carries the real
    answer - X One Design, Squib, Redwing, Daring, Sonar. Without it, XOD X173
    split into HEYDAY and TONIC: one hull, one owner, renamed and renamed
    back. Class labels naming a handicap division or a trophy are filtered out.

  * Types match by CONTAINMENT, not equality, after folding roman numerals.
    "S&S 52" and "S&S 52 Yawl" are one boat; so are "Figaro II" and "Beneteau
    Figaro 2", "Squib" and "National Squib", "Ker 40" and "KER 40 3.0". Demand
    exact equality and the same class written two ways reads as proof of
    difference.

  * A postal address is not a type. One import slipped a column and put the
    owner's home town there; scripts/drop_column_shifted_entries.py removes
    those rows, and this ignores the shape in case it happens again.

The group that keeps the record and its sail number is the one with the most
SOLENT entries, not simply the most entries. This is a Solent market-share
database: when a local boat and a boat met once at Hamilton Island share a
number, the local one is the one whose identity matters. Every other group
becomes a boat with NO sail number, because we genuinely do not know one - the
source printed a number that turned out to be shared, and inventing a unique
one would be worse.

Derived, not curated: it re-reads the entries every run, so a re-scrape that
re-fuses them is re-split. boat_split_origin records which (shared number,
name) each record came from, which is what makes a second run a no-op instead
of a second set of records.

    python scripts/split_shared_sail_numbers.py db/marketshare.db
    python scripts/split_shared_sail_numbers.py db/marketshare.db --apply
"""
import argparse, collections, datetime, pathlib, re, shutil, sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS boat_split_origin (
    boat_id      INTEGER NOT NULL REFERENCES boats(id) ON DELETE CASCADE,
    from_sail_no TEXT NOT NULL,     -- the shared number these entries arrived on
    name_key     TEXT NOT NULL,     -- the normalised name that identifies this boat
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_bso_unique
    ON boat_split_origin(from_sail_no, name_key);
"""

HAS_LETTER = re.compile(r"[A-Za-z]")
# '(S) WIGHT TIGER' and 'WIGHT TIGER' are one Squib: the Cowes points scrape
# flags the class in front of the name in some seasons and not others.
CLASS_FLAG = re.compile(r"^\(\s*[A-Za-z]\s*\)\s*")
ADDRESS = re.compile(r"(United States of America|Canada|United Kingdom)\s*$", re.I)
# a class label naming a handicap division, a fleet or a trophy is not a boat
# type, however much it looks like one
CLASS_NOT_A_TYPE = re.compile(
    r"^(IRC|ORC|PHRF|CHS|NHC|IRM|CLASS|DIV|DIVISION|GROUP|FLEET|CRUISER|CRUISING|RACING|"
    r"WHITE|BLACK|GOLD|SILVER|BLUE|RED|MONOHULL|MULTIHULL|OVERALL|LINE|HANDICAP)\b", re.I)
CLASS_IS_A_PRIZE = re.compile(
    r"TROPHY|OVERALL|NEWCOMER|UNDER ?25|CUP|BOWL|SALVER|PLATE|SHIELD|PRIZE|CHALLENGE|"
    r"SERIES|REGATTA|POINTS", re.I)
ROMAN = {"I": "1", "II": "2", "III": "3", "IV": "4", "V": "5", "VI": "6"}


def key(s):
    return re.sub(r"[^A-Z0-9]", "", CLASS_FLAG.sub("", (s or "")).upper())


def blank_type(t):
    return (not t) or t.strip() in ("-", "") or bool(ADDRESS.search(t))


def type_of(boat_type, cls):
    """The best evidence of what this boat IS, from the two columns that hold it."""
    if not blank_type(boat_type):
        return boat_type
    c = (cls or "").strip()
    if c and not CLASS_NOT_A_TYPE.match(c) and not CLASS_IS_A_PRIZE.search(c):
        return c
    return None


def normtype(t):
    t = re.sub(r"\b(I{1,3}|IV|VI?)\b", lambda m: ROMAN[m.group(1).upper()], t or "", flags=re.I)
    return re.sub(r"[^A-Z0-9]", "", t.upper())


def types_agree(a, b):
    """One type inside the other is agreement, not difference."""
    if a == b:
        return True
    lo, hi = (a, b) if len(a) <= len(b) else (b, a)
    return len(lo) >= 4 and lo in hi



def display_name(ents):
    """The name to put on the record. A Cowes class flag - '(S) ' for Squib -
    is the scrape's annotation, not part of the name, so a spelling without one
    wins even when the flagged spelling is commoner; if every entry carries it,
    it comes off."""
    counts = collections.Counter(e[1] for e in ents if e[1])
    clean = [n for n in counts if not CLASS_FLAG.match(n)]
    if clean:
        return max(clean, key=lambda n: counts[n])
    return CLASS_FLAG.sub("", max(counts, key=counts.get)).strip()


def fuse_by_type(by_name):
    keys = list(by_name)
    types = {k: {normtype(t) for t in (type_of(e[2], e[3]) for e in by_name[k]) if t}
             for k in keys}
    parent = {k: k for k in keys}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if any(types_agree(x, y) for x in types[a] for y in types[b]):
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[ra] = rb
    fused = collections.defaultdict(list)
    for k in keys:
        fused[find(k)].extend(by_name[k])
    return dict(fused)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    dbp = pathlib.Path(a.db)
    if a.apply:
        bak = dbp.with_suffix(f".db.bak-split-{datetime.datetime.now():%H%M%S}")
        shutil.copy2(dbp, bak)
        print("backup ->", bak.name)
    con = sqlite3.connect(dbp)
    cur = con.cursor()
    if a.apply:
        cur.executescript(SCHEMA)
    has_origin = bool(cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                  "AND name='boat_split_origin'").fetchone())

    targets = {bid: sail for bid, sail in cur.execute(
        "SELECT id, sail_no FROM boats WHERE sail_no IS NOT NULL")
        if not HAS_LETTER.search(sail)}
    if not targets:
        print("no boat records on a letterless sail number")
        return

    # each entry with the region and season it was sailed in: the keeper is
    # decided on Solent presence, not on raw entry count
    rows = cur.execute("""
        SELECT re.id, re.boat_id, re.boat_name_used, re.boat_type_used, re.class, re.tcc,
               rg.region, ev.season_year, re.race_id
          FROM race_entries re
          JOIN races ra  ON ra.id = re.race_id
          JOIN events ev ON ev.id = ra.event_id
          JOIN regattas rg ON rg.id = ev.regatta_id
         WHERE re.boat_id IN (%s)""" % ",".join("?" * len(targets)), list(targets)).fetchall()

    groups = collections.defaultdict(lambda: collections.defaultdict(list))
    for eid, bid, nm, typ, cls, tcc, region, season, race_id in rows:
        k = key(nm)
        if not k:
            continue                      # nothing to go on; these stay put
        groups[bid][k].append((eid, nm, typ, cls, tcc, region, season, race_id))

    existing = {}
    if has_origin:
        existing = {(f, n): b for b, f, n in cur.execute(
            "SELECT boat_id, from_sail_no, name_key FROM boat_split_origin")}

    def solent(ents):
        return sum(1 for e in ents if (e[5] or "") == "Solent")

    plan, no_solent_keeper, tied = [], [], []
    for bid, by_name in groups.items():
        by_name = fuse_by_type(by_name)
        if len(by_name) < 2:
            continue
        btype = normtype(cur.execute("SELECT boat_type FROM boats WHERE id = ?",
                                     (bid,)).fetchone()[0] or "")

        def rank(k, _by=by_name, _bt=btype):
            ents = _by[k]
            matches = _bt and any(types_agree(normtype(t), _bt)
                                  for t in (type_of(e[2], e[3]) for e in ents) if t)
            return (-solent(ents), -len(ents), -max((e[6] or 0) for e in ents),
                    0 if matches else 1, k)

        order = sorted(by_name, key=rank)
        keeper = order[0]
        typed = {k for k in by_name if any(type_of(e[2], e[3]) for e in by_name[k])}
        movers = [k for k in order[1:] if k in typed]
        if not movers:
            continue
        if len(order) > 1 and rank(order[0])[:3] == rank(order[1])[:3]:
            tied.append(bid)
        for k in order[1:]:
            if k not in typed:
                by_name[keeper].extend(by_name.pop(k))
        plan.append((bid, targets[bid], keeper, movers, by_name))
        if not solent(by_name[keeper]):
            no_solent_keeper.append(bid)

    plan.sort(key=lambda p: -sum(len(v) for v in p[4].values()))
    new_records = sum(len(p[3]) for p in plan)
    moving = sum(len(p[4][k]) for p in plan for k in p[3])
    print(f"{len(plan)} record(s) are several boats; {new_records} boat record(s) to create, "
          f"{moving} entr(y/ies) to move")
    if no_solent_keeper:
        print(f"  {len(no_solent_keeper)} keeper(s) have no Solent entry at all - nobody in the "
              f"group does, so the sail number goes to the biggest: "
              + ", ".join(str(b) for b in no_solent_keeper[:10]))
    if tied:
        print(f"  {len(tied)} record(s) have a tied keeper, decided on the boat's own recorded "
              f"type and then alphabetically: " + ", ".join(str(b) for b in tied[:10]))

    for bid, sail, keeper, movers, by_name in plan[:6]:
        print(f"\n  sail {sail!r} (boat {bid}) -> {len(by_name)} boats")
        ke = by_name[keeper]
        print(f"     keeps  {ke[0][1]!r} ({len(ke)} entries, {solent(ke)} Solent)")
        for k in movers[:4]:
            ents = by_name[k]
            nm = collections.Counter(e[1] for e in ents).most_common(1)[0][0]
            ty = collections.Counter(type_of(e[2], e[3]) for e in ents
                                     if type_of(e[2], e[3])).most_common(1)
            print(f"     splits {nm!r} ({len(ents)} entries"
                  + (f", {ty[0][0]}" if ty else "") + ")")
        if len(movers) > 4:
            print(f"     ... and {len(movers) - 4} more")

    if not a.apply:
        print("\nDRY RUN - pass --apply")
        return

    created = reused = clashed = 0
    for bid, sail, keeper, movers, by_name in plan:
        for k in movers:
            ents = by_name[k]
            nm = display_name(ents)
            ty = collections.Counter(type_of(e[2], e[3]) for e in ents
                                     if type_of(e[2], e[3])).most_common(1)
            tc = collections.Counter(e[4] for e in ents if e[4] is not None).most_common(1)
            hit = existing.get((sail, k))
            if hit:
                new_id, reused = hit, reused + 1
            else:
                cur.execute("INSERT INTO boats (sail_no, boat_name, boat_type, tcc) "
                            "VALUES (NULL, ?, ?, ?)",
                            (nm, ty[0][0] if ty else None, tc[0][0] if tc else None))
                new_id = cur.lastrowid
                cur.execute("INSERT INTO boat_split_origin (boat_id, from_sail_no, name_key) "
                            "VALUES (?,?,?)", (new_id, sail, k))
                created += 1
            for e in ents:
                # A re-run after a scrape can find the target already holding a
                # row for this race. UNIQUE(race_id, boat_id) would abort the
                # whole apply, so the entry is left where it is and counted.
                if cur.execute("SELECT 1 FROM race_entries WHERE race_id = ? AND boat_id = ?",
                               (e[7], new_id)).fetchone():
                    clashed += 1
                    continue
                cur.execute("UPDATE race_entries SET boat_id = ? WHERE id = ?", (new_id, e[0]))
        # the keeper's own identity, now that it is one boat. Not COALESCE: a
        # type or a rating only the departed entries carried is not the
        # keeper's, and null is the honest answer.
        ents = by_name[keeper]
        nm = display_name(ents)
        ty = collections.Counter(type_of(e[2], e[3]) for e in ents
                                 if type_of(e[2], e[3])).most_common(1)
        tc = collections.Counter(e[4] for e in ents if e[4] is not None).most_common(1)
        cur.execute("UPDATE boats SET boat_name = ?, boat_type = ?, tcc = ?, "
                    "updated_at = datetime('now') WHERE id = ?",
                    (nm, ty[0][0] if ty else None, tc[0][0] if tc else None, bid))
    con.commit()
    print(f"\ncreated {created} boat record(s), re-used {reused} from an earlier run, "
          f"moved {moving - clashed} entr(y/ies)"
          + (f"; {clashed} left in place, the target already had that race" if clashed else ""))


if __name__ == "__main__":
    main()
