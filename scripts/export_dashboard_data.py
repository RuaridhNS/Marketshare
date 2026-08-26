#!/usr/bin/env python3
"""Export the SQLite database to a single JSON file consumed by the
self-contained dashboard.html. Run this after any DB update, then run
build_dashboard.py to bake the fresh JSON into the HTML file."""
import re
import sys
import json
import sqlite3
import datetime

def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else "/home/claude/marketshare/db/marketshare.db"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "/home/claude/marketshare/dashboard/data.json"

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    sailmakers = [dict(r) for r in cur.execute("SELECT id, name, is_us FROM sailmakers")]
    sm_by_id = {s["id"]: s for s in sailmakers}

    owners = [dict(r) for r in cur.execute("SELECT id, name FROM owners")]
    owner_by_id = {o["id"]: o for o in owners}

    regattas = [dict(r) for r in cur.execute("SELECT id, name, category, region FROM regattas ORDER BY name")]

    events = [dict(r) for r in cur.execute(
        "SELECT id, regatta_id, season_year, start_date, end_date, source_url, notes FROM events")]

    races = [dict(r) for r in cur.execute(
        "SELECT id, event_id, race_name, race_number, race_date, status, source_url FROM races")]

    class_counts = [dict(r) for r in cur.execute(
        "SELECT event_id, class_label, entry_count, source FROM event_class_counts")]

    boats_rows = cur.execute("""
        SELECT b.id, b.sail_no, b.boat_name, b.boat_type, b.tcc,
               o.name AS owner_name,
               bc.lead_rep, bc.contacted_by, bc.in_cs, bc.tag, bc.notes AS crm_notes
        FROM boats b
        LEFT JOIN owners o ON o.id = b.current_owner_id
        LEFT JOIN boat_crm bc ON bc.boat_id = b.id
        ORDER BY b.boat_name
    """).fetchall()

    # Deliberately NOT denormalizing race_name/season_year/regatta_id/
    # regatta_name/regatta_category onto every entry here - the dashboard
    # already ships DATA.races/DATA.events/DATA.regattas as lookup tables
    # (racesById/eventsById/regattasById), so entries just carry race_id and
    # derive the rest client-side. At tens of thousands of entries this was
    # a meaningful chunk of the embedded JSON's size for zero new information.
    entries_rows = cur.execute("""
        SELECT re.boat_id, re.race_id, re.class, re.sail_no_used, re.boat_name_used, re.boat_type_used,
               re.tcc, re.owner_name_used, re.skipper_name_used, re.sailmaker_id, re.status,
               re.finish_time, re.elapsed_time, re.corrected_time, re.position, re.points,
               re.comments, re.tag, re.source
        FROM race_entries re
    """).fetchall()

    # ---- IRC-only scope ----------------------------------------------------
    # This tool tracks the IRC fleet. Pure one-design boats (XOD, Squib,
    # Sunbeam, SB20, most J/70s) are filtered out here rather than deleted, so
    # the call stays reversible and a mis-classified boat can be recovered by
    # re-running the export.
    #
    # A boat counts as IRC if it has EITHER raced in an IRC-labelled class at
    # some point, OR has an IRC TCC on file. Classifying by boat rather than by
    # class label matters: J/109s, J/111s, Cape 31s and Quarter Tonners hold IRC
    # certs and are core customers, but often get their own one-design start at
    # Cowes and Royal Southern. Those one-design races still count as fleet
    # activity for a boat that qualifies - only boats with no IRC signal at all
    # are dropped.
    irc_class_re = re.compile(r"\bIRC\b", re.I)
    # Boat types that are pure one-design fleets. A TCC on its own does NOT
    # qualify one of these: a handful of SB20s and J/70s carry a stray rating
    # yet have never started an IRC race, and they were leaking through.
    OD_TYPE_RE = re.compile(
        r"^\s*(sb\s?20|j\s?/?70|j\s?/?80|x\s?od|x one design|squib|sunbeam|dragon|"
        r"etchells|daring|sonar|mermaid|redwing|victory|flying\s?15|swallow|"
        r"rs\s?elite|rs\s?21|cork\s?1720|contessa\s?32|sonata|folkboat)\b", re.I)

    # First clause: actually started an IRC race - the strongest possible signal.
    irc_boat_ids = {e["boat_id"] for e in entries_rows
                    if e["class"] and irc_class_re.search(e["class"])}
    # Second clause: holds an IRC rating and isn't a one-design. This rescues
    # boats whose entries carry no class label at all (~13k rows, mostly RORC),
    # which would otherwise be dropped despite plainly being IRC boats.
    irc_boat_ids |= {b["id"] for b in boats_rows
                     if b["tcc"] is not None and not OD_TYPE_RE.match(b["boat_type"] or "")}
    # Override: a one-design hull is not an IRC boat even when it has started an
    # IRC race. A handful do (an XOD with 28 "IRC Class 7" starts at Cowes, a
    # J/70 with 11) - those entries are real, but the fleet is not one we rate
    # or sell IRC sails into, and this tool is scoped to the IRC fleet. Six
    # boats; reverse by dropping this line and re-running the export.
    irc_boat_ids -= {b["id"] for b in boats_rows if OD_TYPE_RE.match(b["boat_type"] or "")}

    n_boats_all = len(boats_rows)
    boats_rows = [b for b in boats_rows if b["id"] in irc_boat_ids]
    entries_rows = [e for e in entries_rows if e["boat_id"] in irc_boat_ids]

    # Aggregate class-count rows are labelled far more tersely ("1", "2", "0"
    # are IRC divisions), so only the explicitly one-design fleets are dropped.
    OD_CLASS_LABELS = {"j/70", "j70", "sb20", "sb 20", "xod", "x one design",
                       "squib", "sunbeam", "dragon", "etchells", "daring",
                       "sonar", "mermaid", "redwing", "victory", "flying 15",
                       "contessa 32", "sonata", "swallow", "rs elite", "j/80"}
    dropped_cc = {c["event_id"] for c in class_counts
                  if c["class_label"].strip().lower() in OD_CLASS_LABELS}
    class_counts = [c for c in class_counts
                    if c["class_label"].strip().lower() not in OD_CLASS_LABELS]
    # A "Total" row that counted those one-design boats is now wrong - rebuild
    # it from the remaining classes for any event we removed a fleet from.
    for eid in dropped_cc:
        rest = [c for c in class_counts
                if c["event_id"] == eid and c["class_label"].lower() != "total"]
        for c in class_counts:
            if c["event_id"] == eid and c["class_label"].lower() == "total":
                c["entry_count"] = sum(x["entry_count"] or 0 for x in rest)

    print(f"IRC filter: kept {len(boats_rows)}/{n_boats_all} boats, "
          f"{len(entries_rows)} entries; recomputed totals for {len(dropped_cc)} event(s)")

    sm_history_rows = cur.execute("""
        SELECT boat_id, sailmaker_id, effective_from, effective_to, source, confidence
        FROM boat_sailmaker_history ORDER BY boat_id, id
    """).fetchall()

    owner_history_rows = cur.execute("""
        SELECT boat_id, owner_id, effective_from, effective_to, source, confidence
        FROM boat_owner_history ORDER BY boat_id, id
    """).fetchall()

    # ---- assemble boats with nested entries + sailmaker history ----
    races_by_id = {r["id"]: r for r in races}
    events_by_id = {e["id"]: e for e in events}

    def entry_season_year(entry):
        race = races_by_id.get(entry["race_id"])
        ev = events_by_id.get(race["event_id"]) if race else None
        return ev["season_year"] if ev else None

    entries_by_boat = {}
    for r in entries_rows:
        d = dict(r)
        d["sailmaker_name"] = sm_by_id.get(d["sailmaker_id"], {}).get("name") if d["sailmaker_id"] else None
        entries_by_boat.setdefault(d["boat_id"], []).append(d)

    smhist_by_boat = {}
    for r in sm_history_rows:
        d = dict(r)
        d["sailmaker_name"] = sm_by_id.get(d["sailmaker_id"], {}).get("name") if d["sailmaker_id"] else None
        smhist_by_boat.setdefault(d["boat_id"], []).append(d)

    ownerhist_by_boat = {}
    for r in owner_history_rows:
        d = dict(r)
        d["owner_name"] = owner_by_id.get(d["owner_id"], {}).get("name") if d["owner_id"] else None
        ownerhist_by_boat.setdefault(d["boat_id"], []).append(d)

    boats = []
    for b in boats_rows:
        d = dict(b)
        d["entries"] = sorted(entries_by_boat.get(d["id"], []),
                               key=lambda e: (entry_season_year(e) or 0), reverse=True)
        d["sailmaker_history"] = smhist_by_boat.get(d["id"], [])
        d["owner_history"] = ownerhist_by_boat.get(d["id"], [])
        # "current" sailmaker = most recent history row, else most recent entry's sailmaker
        current_sm = None
        if d["sailmaker_history"]:
            current_sm = d["sailmaker_history"][-1]["sailmaker_name"]
        elif d["entries"]:
            for e in d["entries"]:
                if e["sailmaker_name"]:
                    current_sm = e["sailmaker_name"]
                    break
        d["current_sailmaker"] = current_sm
        boats.append(d)

    # ---- market share: boat-level entries with a known sailmaker ----
    from collections import Counter, defaultdict
    sm_counts = Counter()
    for e in entries_rows:
        if e["sailmaker_id"]:
            sm_counts[sm_by_id[e["sailmaker_id"]]["name"]] += 1
    market_share = [{"sailmaker": k, "entries": v} for k, v in sorted(sm_counts.items(), key=lambda x: -x[1])]

    # distinct-boat market share (a boat counted once, using its "current" sailmaker)
    boat_sm_counts = Counter(b["current_sailmaker"] for b in boats if b["current_sailmaker"])
    market_share_boats = [{"sailmaker": k, "boats": v} for k, v in sorted(boat_sm_counts.items(), key=lambda x: -x[1])]

    # ---- entry trends: per regatta, per year, total entries (from event_class_counts) ----
    cc_by_event = defaultdict(list)
    for c in class_counts:
        cc_by_event[c["event_id"]].append(c)

    trend_rows = []
    for ev in events:
        rows = cc_by_event.get(ev["id"], [])
        total_row = next((r for r in rows if r["class_label"].lower() == "total"), None)
        if total_row:
            total = total_row["entry_count"]
        elif rows:
            total = sum(r["entry_count"] or 0 for r in rows)
        else:
            total = None
        if total is not None:
            trend_rows.append({"regatta_id": ev["regatta_id"], "season_year": ev["season_year"], "total": total})

    regattas_by_id = {r["id"]: r for r in regattas}
    for t in trend_rows:
        t["regatta_name"] = regattas_by_id[t["regatta_id"]]["name"]
        t["regatta_category"] = regattas_by_id[t["regatta_id"]]["category"]

    stats = {
        "n_boats": len(boats),
        "n_entries": len(entries_rows),
        "n_regattas": len(regattas),
        "n_events": len(events),
        "n_races": len(races),
        "n_class_count_rows": len(class_counts),
    }

    data = {
        "generated_at": datetime.datetime.now().isoformat(),
        "stats": stats,
        "sailmakers": sailmakers,
        "regattas": regattas,
        "events": events,
        "races": races,
        "boats": boats,
        "market_share_entries": market_share,
        "market_share_boats": market_share_boats,
        "entry_trends": trend_rows,
        "class_counts": class_counts,
    }

    # Most entry/history fields are null on any given row (RORC/Cowes/Royal
    # Southern don't all publish the same columns) - at tens of thousands of
    # rows, the null VALUES cost little, but the repeated null KEY NAMES add
    # up fast in JSON. Dropping null keys is safe: every JS read of these is
    # already `e.field` / `e.field || x` / `e.field ? ... `, and a missing
    # key reads as undefined, which behaves identically to null there.
    def strip_nulls(d):
        return {k: v for k, v in d.items() if v is not None}

    for b in data["boats"]:
        b["entries"] = [strip_nulls(e) for e in b["entries"]]
        b["sailmaker_history"] = [strip_nulls(h) for h in b["sailmaker_history"]]
        b["owner_history"] = [strip_nulls(h) for h in b["owner_history"]]

    with open(out_path, "w") as f:
        json.dump(data, f, default=str)
    print(f"Wrote {out_path} ({len(boats)} boats, {len(entries_rows)} entries, {len(trend_rows)} trend rows)")

if __name__ == "__main__":
    main()
