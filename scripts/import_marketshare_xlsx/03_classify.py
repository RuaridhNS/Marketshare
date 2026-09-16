#!/usr/bin/env python3
"""Decide, per event, whether it is UK or already in the database, and how many
of its boats can be matched to boats we already hold.

The rule from the brief: a UK or already-drafted event must have its boats
MATCHED to existing records or discarded, so that a hand-kept spreadsheet can
never fork the boat table. Everything else is a genuinely new event.
"""
import csv, re, sqlite3, collections, pathlib

DB = r"C:\Solent Marketshare\marketshare_project\marketshare\db\marketshare.db"

# ---- normalisers -------------------------------------------------------------
def nname(s):
    s = (s or "").strip().lower()
    s = re.sub(r"[\x00-\x1f\x7f-\x9f]+", " ", s)
    s = re.sub(r"\b(the|a|an)\b", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def nsail(s):
    s = (s or "").strip().upper()
    s = re.sub(r"[^A-Z0-9]", "", s)
    return s or None

def sail_core(s):
    """GBR1234R -> 1234 ; keeps the digits that actually identify the boat."""
    s = nsail(s)
    if not s:
        return None
    m = re.search(r"(\d{2,6})", s)
    return m.group(1) if m else None

# ---- which events are UK / already drafted -----------------------------------
UK_PAT = re.compile(
    r"\bcowes\b|\bsolent\b|round the island|\bhamble\b|\bwarsash\b|"
    r"royal southern|\brtyc\b|\bjog\b|\buk\b|\bgbr\b|\bbritish\b|"
    r"\bfastnet\b|myth of malham|de guingand|cervantes|morgan cup|"
    r"rorc channel race|cherbourg race|castle rock|vice admiral|"
    r"round britain|north sea race|\brnsa\b|half ton|one ton|"
    r"\bpoole\b|\bweymouth\b|\bdartmouth\b|\bplymouth\b|\bfalmouth\b|"
    r"\btorbay\b|\bramsgate\b|\bpwllheli\b|\bportishead\b|\btarbert\b|"
    r"cowes dinard|dinard st malo|easter challenge|"
    r"taittinger|\bxod\b|\bIRC Nationals\b", re.I)

# names that look UK but are not
NOT_UK = re.compile(
    r"essex rum|balena bay|san francisco|annapolis|newport|chicago|detroit|"
    r"marblehead|kingston|halifax|sydney|hobart|auckland|antigua|bvi|"
    r"caribbean|bermuda|st barth|porto|mallorca|palma|sardinia|"
    r"middle sea|giraglia|saint-tropez|antibes|kiel|gotland|silverrudder", re.I)

def is_uk(name):
    if NOT_UK.search(name):
        return False
    return bool(UK_PAT.search(name))

def main():
    con = sqlite3.connect(DB)
    # --- existing regattas, for the "already drafted" test
    regattas = [(r[0], r[1]) for r in con.execute("select id,name from regattas")]
    reg_by_norm = {}
    for rid, rn in regattas:
        reg_by_norm.setdefault(nname(rn), rid)

    # --- existing boats, for matching
    boats = list(con.execute(
        "select id, sail_no, boat_name, boat_type from boats"))
    by_sail, by_core, by_name = {}, collections.defaultdict(list), collections.defaultdict(list)
    for bid, sn, bn, bt in boats:
        k = nsail(sn)
        if k:
            by_sail.setdefault(k, bid)
        c = sail_core(sn)
        if c:
            by_core[c].append((bid, nname(bn)))
        if bn:
            by_name[nname(bn)].append((bid, nsail(sn)))
    print(f"db boats={len(boats):,}  regattas={len(regattas)}")

    events = list(csv.DictReader(open("staging_events.csv", encoding="utf-8")))
    entries = list(csv.DictReader(open("staging_entries.csv", encoding="utf-8")))
    by_file = collections.defaultdict(list)
    for e in entries:
        by_file[e["event_key"]].append(e)

    out = []
    for ev in events:
        nm = ev["event_name"]
        n = nname(nm)
        drafted = None
        for rn, rid in reg_by_norm.items():
            if not rn:
                continue
            if rn == n:
                drafted = rid
                break
            if len(rn) > 8 and (contains_words(n, rn) or contains_words(rn, n)):
                drafted = rid
                break
        uk = is_uk(nm)
        rows = by_file[ev["event_key"]]
        matched = unmatched = 0
        if uk or drafted:
            for r in rows:
                if match_boat(r, by_sail, by_core, by_name):
                    matched += 1
                else:
                    unmatched += 1
        out.append({**ev, "is_uk": int(uk), "drafted_regatta_id": drafted or "",
                    "matched": matched, "unmatched": unmatched,
                    "disposition": ("match-or-discard" if (uk or drafted) else "new event")})

    with open("staging_events_classified.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0].keys()))
        w.writeheader(); w.writerows(out)

    mo = [o for o in out if o["disposition"] == "match-or-discard"]
    new = [o for o in out if o["disposition"] == "new event"]
    print(f"\nmatch-or-discard events : {len(mo):3d}   entries {sum(int(o['entries']) for o in mo):,}")
    print(f"   matched   {sum(o['matched'] for o in mo):,}")
    print(f"   discarded {sum(o['unmatched'] for o in mo):,}")
    print(f"new events              : {len(new):3d}   entries {sum(int(o['entries']) for o in new):,}")
    print("\n-- match-or-discard detail --")
    for o in sorted(mo, key=lambda r: -int(r["entries"])):
        tot = o["matched"] + o["unmatched"]
        pct = 100 * o["matched"] / tot if tot else 0
        flag = "DB" if o["drafted_regatta_id"] else "UK"
        print(f"  {flag} {o['year'] or '????'} {int(o['entries']):>4}  "
              f"match {o['matched']:>4} ({pct:4.0f}%)  {o['event_name'][:52]}")

def contains_words(hay, needle):
    """Whole-word containment, so 'round island race' does not match inside
    'around island race'."""
    h, nd = hay.split(), needle.split()
    if len(nd) > len(h):
        return False
    return any(h[i:i + len(nd)] == nd for i in range(len(h) - len(nd) + 1))


def match_boat(r, by_sail, by_core, by_name):
    s = nsail(r.get("sail_no"))
    if s and s in by_sail:
        return by_sail[s]
    nm = nname(r.get("boat_name"))
    c = sail_core(r.get("sail_no"))
    if c and c in by_core:
        cands = by_core[c]
        if nm:
            for bid, bn in cands:
                if bn == nm:
                    return bid
        # A bare sail-number core is NOT enough on its own: one-design fleets
        # reuse plain numbers, so "1234" can be a J/70 and a Lightning. Only
        # accept it when the row carries no boat name to check against.
        if len(cands) == 1 and not nm:
            return cands[0][0]
    if nm and nm in by_name:
        cands = by_name[nm]
        if len(cands) == 1:
            return cands[0][0]
        if c:
            for bid, sn in cands:
                if sn and c in sn:
                    return bid
    return None

if __name__ == "__main__":
    main()
