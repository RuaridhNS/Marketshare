#!/usr/bin/env python3
"""Load the Marketshare workbook corpus into the database.

Two dispositions, per the brief:

  match-or-discard  UK events, and events already drafted in the database.
                    Each boat is matched to a boat we already hold; a row that
                    cannot be matched is DISCARDED rather than inserted, so a
                    hand-kept spreadsheet can never fork the boat table.

  new event         Everything else. A new regatta, event, entry-list "race"
                    and, where needed, new boats.

Idempotent: re-running replaces only what this loader created, which it tracks
through races.source_url = 'marketshare-xlsx:<relative path>'.
"""
import csv, re, sqlite3, collections, pathlib, datetime, shutil, sys

DB = pathlib.Path(r"C:\Solent Marketshare\marketshare_project\marketshare\db\marketshare.db")
TAG = "marketshare-xlsx:"
from classify import nname, nsail, sail_core, match_boat

# ---- sailmaker normalisation -------------------------------------------------
SM_ALIAS = {
    "north": "North Sails", "north sails": "North Sails", "ns": "North Sails",
    "n/s": "North Sails", "3di": "North Sails", "north full": "North Sails",
    "ns it": "North Sails", "north 3di": "North Sails", "northsails": "North Sails",
    "partial": "Partial", "partial north": "Partial", "mixed": "Partial",
    "doyle": "Doyle", "quantum": "Quantum", "uk": "UK", "uk sails": "UK",
    "uk sailmakers": "UK", "evolution": "Evolution", "one sails": "One",
    "onesails": "One", "one": "One", "ullman": "Ullman", "incidence": "Incidence",
    "inc": "Incidence", "elvstrom": "Elvstrom", "elvström": "Elvstrom",
    "sanders": "Sanders", "technique voile": "Technique Voile",
    "all purpose": "All Purpose", "delta voiles": "Delta Voiles",
    "bay": "Bay Sails", "fritz segel": "Fritz Segel", "gaastra": "Gaastra",
    "olimpic": "Olimpic", "hyde": "Hyde", "banks": "Banks", "relling": "Relling",
    "gp": "GP", "unknown": "Unknown", "unknow": "Unknown", "unkown": "Unknown",
    "other": "Other", "service": "Unknown",
}
def norm_sm(s):
    k = re.sub(r"\s+", " ", (s or "").strip().lower())
    if not k:
        return None
    if k in SM_ALIAS:
        return SM_ALIAS[k]
    if k.startswith("north") or k.startswith("ns "):
        return "North Sails"
    if k.startswith("partial"):
        return "Partial"
    for a, v in SM_ALIAS.items():
        if len(a) > 3 and a in k:
            return v
    return "Other"

def main(apply=False):
    if apply:
        bak = DB.with_suffix(f".db.bak-xlsximport-{datetime.datetime.now():%H%M%S}")
        shutil.copy2(DB, bak)
        print("backup ->", bak.name)
    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys=ON")
    cur = con.cursor()

    # --- schema: segment on regattas ------------------------------------------
    cols = [r[1] for r in cur.execute("pragma table_info(regattas)")]
    if "segment" not in cols:
        cur.execute("alter table regattas add column segment TEXT")
        print("added regattas.segment")
    if "country" not in cols:
        cur.execute("alter table regattas add column country TEXT")
        print("added regattas.country")

    # --- wipe anything a previous run of this loader made ---------------------
    old = [r[0] for r in cur.execute(
        "select id from races where source_url like ?", (TAG + "%",))]
    if old:
        q = ",".join("?" * len(old))
        cur.execute(f"delete from race_entries where race_id in ({q})", old)
        cur.execute(f"delete from races where id in ({q})", old)
        print(f"cleared {len(old)} races from a previous import")

    # --- lookups ---------------------------------------------------------------
    sm_id = {n.lower(): i for i, n in cur.execute("select id,name from sailmakers")}
    def sailmaker_id(s):
        v = norm_sm(s)
        if not v:
            return None
        if v.lower() not in sm_id:
            cur.execute("insert into sailmakers(name,notes) values(?,?)",
                        (v, "added by Marketshare workbook import"))
            sm_id[v.lower()] = cur.lastrowid
        return sm_id[v.lower()]

    boats = list(cur.execute("select id, sail_no, boat_name, boat_type from boats"))
    by_sail, by_core, by_name = {}, collections.defaultdict(list), collections.defaultdict(list)
    def index(bid, sn, bn):
        k = nsail(sn)
        if k:
            by_sail.setdefault(k, bid)
        c = sail_core(sn)
        if c:
            by_core[c].append((bid, nname(bn)))
        if bn:
            by_name[nname(bn)].append((bid, nsail(sn)))
    for bid, sn, bn, bt in boats:
        index(bid, sn, bn)

    reg_id = {nname(n): i for i, n in cur.execute("select id,name from regattas")}

    events = list(csv.DictReader(open("staging_events_segmented.csv", encoding="utf-8")))
    entries = list(csv.DictReader(open("staging_entries.csv", encoding="utf-8")))
    by_file = collections.defaultdict(list)
    for e in entries:
        by_file[e["event_key"]].append(e)

    stat = collections.Counter()
    for ev in events:
        rows = by_file[ev["event_key"]]
        if not rows:
            continue
        year = int(ev["year"]) if (ev["year"] or "").isdigit() else None
        seg = ev["segment"]
        mod = ev["disposition"] == "match-or-discard"

        if mod:
            rid = ev["drafted_regatta_id"]
            rid = int(rid) if str(rid).isdigit() else reg_id.get(nname(ev["event_name"]))
            if not rid:
                cur.execute("insert into regattas(name,category,region,segment,country)"
                            " values(?,?,?,?,?)",
                            (ev["event_name"], "Marketshare", "UK", seg, "GBR"))
                rid = cur.lastrowid
                reg_id[nname(ev["event_name"])] = rid
            else:
                cur.execute("update regattas set segment=coalesce(segment,?) where id=?",
                            (seg, rid))
        else:
            key = nname(ev["event_name"])
            rid = reg_id.get(key)
            if not rid:
                cur.execute("insert into regattas(name,category,region,segment,country)"
                            " values(?,?,?,?,?)",
                            (ev["event_name"], "Marketshare", "Non-UK", seg, None))
                rid = cur.lastrowid
                reg_id[key] = rid
            else:
                cur.execute("update regattas set segment=coalesce(segment,?) where id=?",
                            (seg, rid))

        # event for the season
        r = cur.execute("select id from events where regatta_id=? and season_year is ?",
                        (rid, year)).fetchone()
        if r:
            eid = r[0]
        else:
            cur.execute("insert into events(regatta_id,season_year,start_date,end_date,"
                        "source_url,notes) values(?,?,?,?,?,?)",
                        (rid, year, ev["start_date"] or None, ev["end_date"] or None,
                         TAG + ev["src_file"], "imported from Marketshare workbook"))
            eid = cur.lastrowid

        cur.execute("insert into races(event_id,race_name,race_number,race_date,"
                    "status,source_url) values(?,?,?,?,?,?)",
                    (eid, "Entry List", 1, ev["start_date"] or None,
                     "entry-list", TAG + ev["src_file"]))
        race_id = cur.lastrowid

        seen = set()          # one boat can only appear once per entry list
        for row in rows:
            bid = match_boat(row, by_sail, by_core, by_name)
            if not bid:
                if mod:
                    stat["discarded"] += 1
                    continue
                bn = (row.get("boat_name") or "").strip() or None
                sn = (row.get("sail_no") or "").strip() or None
                if not (bn or sn):
                    stat["discarded"] += 1
                    continue
                cur.execute("insert into boats(sail_no,boat_name,boat_type)"
                            " values(?,?,?)",
                            (sn, bn, (row.get("boat_type") or "").strip() or None))
                bid = cur.lastrowid
                index(bid, sn, bn)
                stat["new boats"] += 1
            else:
                stat["matched"] += 1
            if bid in seen:
                stat["dup rows"] += 1
                continue
            seen.add(bid)
            cur.execute(
                "insert into race_entries(race_id,boat_id,class,sail_no_used,"
                "boat_name_used,boat_type_used,owner_name_used,skipper_name_used,"
                "sailmaker_id,source) values(?,?,?,?,?,?,?,?,?,?)",
                (race_id, bid, row.get("division") or None,
                 row.get("sail_no") or None, row.get("boat_name") or None,
                 row.get("boat_type") or None, row.get("owner") or None,
                 row.get("skipper") or None, sailmaker_id(row.get("sailmaker")),
                 TAG + ev["src_file"]))
            stat["entries"] += 1

    print("\n" + "\n".join(f"  {k:<12} {v:,}" for k, v in sorted(stat.items())))
    if apply:
        con.commit()
        print("\ncommitted")
        for line in cur.execute(
                "select segment, count(distinct r.id), count(distinct re.boat_id) "
                "from regattas r join events e on e.regatta_id=r.id "
                "join races ra on ra.event_id=e.id "
                "join race_entries re on re.race_id=ra.id "
                "where r.segment is not null group by 1 order by 3 desc"):
            print(f"  {line[0]:<16} {line[1]:>4} regattas  {line[2]:>7,} boats")
        print("\ntotals:",
              dict(zip(("boats", "regattas", "events", "races", "entries"),
                       [cur.execute(f"select count(*) from {t}").fetchone()[0]
                        for t in ("boats", "regattas", "events", "races", "race_entries")])))
    else:
        con.rollback()
        print("\nDRY RUN - nothing written (pass --apply)")

if __name__ == "__main__":
    main("--apply" in sys.argv)
