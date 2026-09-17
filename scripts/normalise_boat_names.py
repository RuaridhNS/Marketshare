#!/usr/bin/env python3
"""Give every boat one official name: the one it last raced under.

A boat cannot have two names. The record name is the most recent name from the
boat's own entry lists - unless the boat is out on charter under a temporary
name, in which case the name it carried before the charter stands.

Three rules, in order:

  1. data/boat_name_overrides.csv wins outright, for the cases no rule can get
     right on its own.
  2. If the latest season's entries were filed by a charter operator
     (owners.is_charter_operator) and the name changed at the same time, that
     is a charter name: keep the last name from before the charter.
  3. Otherwise take the name from the latest season - specifically the one the
     boat finished that season under, so a mid-season rename resolves to the
     new name. Which SPELLING of it becomes official is decided by weight of
     evidence across every season, not by the last entry typed, or a typo in
     the final regatta of the year wins.

A boat whose latest season holds two unrelated names still gets the most recent
one, but is reported: a hull cannot be in two places at once, so a record like
that is often several boats merged on a shared sail number and worth splitting.

    python scripts/normalise_boat_names.py db/marketshare.db            # dry run
    python scripts/normalise_boat_names.py db/marketshare.db --apply
"""
import argparse, collections, csv, datetime, difflib, pathlib, re, shutil, sqlite3

OVERRIDES = pathlib.Path(__file__).resolve().parents[1] / "data" / "boat_name_overrides.csv"


def norm(s):
    """Compare names ignoring case, punctuation and spacing, so TESSA3 and
    TESSA 3 are the same name rather than a rename."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def similar(a, b):
    """True when two names in one season are really the same name typed twice."""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return True
    if na == nb or na.startswith(nb) or nb.startswith(na):
        return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= 0.85


def load_overrides():
    out = {}
    if not OVERRIDES.exists():
        return out
    for r in csv.DictReader(OVERRIDES.open(encoding="utf-8-sig")):
        key = (r.get("BoatId") or "").strip()
        if key.isdigit() and (r.get("OfficialName") or "").strip():
            out[int(key)] = r["OfficialName"].strip()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    dbp = pathlib.Path(a.db)
    if a.apply:
        bak = dbp.with_suffix(f".db.bak-names-{datetime.datetime.now():%H%M%S}")
        shutil.copy2(dbp, bak)
        print("backup ->", bak.name)
    con = sqlite3.connect(dbp)
    cur = con.cursor()

    overrides = load_overrides()
    print(f"overrides on file: {len(overrides)}")

    # every (boat, season, name) with how often it was used and how late in the
    # season it last appeared, newest first. A boat renamed mid-season keeps the
    # name it finished the season under, which is the whole point of the rule.
    rows = cur.execute("""
        select re.boat_id, e.season_year, re.boat_name_used, count(*) n,
               max(coalesce(o.is_charter_operator, 0)) charter,
               max(coalesce(ra.race_date, e.start_date, '')) last_seen,
               max(re.id) last_id
          from race_entries re
          join races ra on ra.id = re.race_id
          join events e  on e.id  = ra.event_id
          left join owners o on o.id = re.owner_id
         where re.boat_name_used is not null and trim(re.boat_name_used) <> ''
           and e.season_year is not null
         group by re.boat_id, e.season_year, re.boat_name_used
         order by re.boat_id, e.season_year desc, last_seen desc, last_id desc""").fetchall()

    per_boat = collections.OrderedDict()
    for bid, yr, name, n, charter, last_seen, last_id in rows:
        per_boat.setdefault(bid, []).append((yr, name, n, charter, last_seen, last_id))

    current = {b: (nm, sn) for b, nm, sn in
               cur.execute("select id, boat_name, sail_no from boats")}

    changes, charter_kept, ambiguous, unchanged = [], [], [], 0
    for bid, hist in per_boat.items():
        rec = current.get(bid, ("", ""))[0]

        if bid in overrides:
            official, why = overrides[bid], "override"
        else:
            top_year = hist[0][0]
            season = [h for h in hist if h[0] == top_year]
            # hist is ordered latest-first, so season[0] is the name the boat
            # finished the season under - a mid-season rename resolves to the
            # new name, which is what "most recent name" means.
            latest = season[0][1]
            # The NAME comes from the latest season; its SPELLING is settled by
            # weight of evidence across the boat's whole history. Otherwise a
            # typo in the last regatta of the year wins on a tie - "MINT JUELP"
            # (3 entries, one season) beat "MINT JULEP" (13, five seasons).
            totals = collections.Counter()
            for h in hist:
                totals[h[1]] += h[2]
            variants = [h for h in season if similar(h[1], latest)]
            official = max(variants, key=lambda h: (totals[h[1]], h[2]))[1]
            why = "latest season"
            # A season carrying several unrelated names is worth a human look,
            # but the rule still applies - it is reported, not skipped.
            if any(not similar(latest, h[1]) for h in season):
                ambiguous.append((bid, current.get(bid, ("", ""))[1], top_year,
                                  [h[1] for h in season], official))

            # charter exception: a temporary name filed by a charter operator
            if season[0][3] and norm(official) != norm(rec):
                prior = next((h[1] for h in hist if h[0] < top_year
                              and norm(h[1]) != norm(official)), None)
                if prior:
                    charter_kept.append((bid, official, prior))
                    official, why = prior, "charter name ignored"

        if norm(official) == norm(rec) and official == rec:
            unchanged += 1
            continue
        changes.append((bid, rec, official, why))

    # ---- report
    renames = [c for c in changes if norm(c[1]) != norm(c[2])]
    respell = [c for c in changes if norm(c[1]) == norm(c[2])]
    print(f"\nboats with entries      : {len(per_boat):,}")
    print(f"already correct         : {unchanged:,}")
    print(f"to update               : {len(changes):,}")
    print(f"   real renames         : {len(renames):,}")
    print(f"   spelling/spacing only: {len(respell):,}")
    print(f"charter names ignored   : {len(charter_kept)}")
    print(f"several names in a season, rule still applied: {len(ambiguous)}")

    print("\n-- sample renames --")
    for bid, was, now, why in renames[:12]:
        print(f"   {bid:<6} {was!r} -> {now!r}")
    if charter_kept:
        print("\n-- charter names NOT adopted (official name kept) --")
        for bid, chartered, kept in charter_kept:
            print(f"   {bid:<6} raced as {chartered!r}, kept {kept!r}")
    if ambiguous:
        print(f"\n-- more than one name in the latest season. The most recent was")
        print(f"   taken. Check these are one boat, not two merged on a shared sail")
        print(f"   number. Showing 10 of {len(ambiguous)} --")
        for bid, sail, yr, names, chosen in ambiguous[:10]:
            print(f"   {bid:<6} {sail or '?':<12} {yr}: "
                  f"{', '.join(repr(n) for n in names[:4])}  -> {chosen!r}")

    if not a.apply:
        print("\nDRY RUN - nothing written (pass --apply)")
        return
    cur.executemany("update boats set boat_name=?, updated_at=datetime('now') where id=?",
                    [(now, bid) for bid, was, now, why in changes])
    con.commit()
    print(f"\ncommitted {len(changes):,} name updates")


if __name__ == "__main__":
    main()
