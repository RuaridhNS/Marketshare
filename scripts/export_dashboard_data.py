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

    owners = [dict(r) for r in cur.execute("SELECT id, name, is_charter_operator FROM owners")]
    owner_by_id = {o["id"]: o for o in owners}

    regattas = [dict(r) for r in cur.execute("SELECT id, name, category, region FROM regattas ORDER BY name")]

    events = [dict(r) for r in cur.execute(
        "SELECT id, regatta_id, season_year, start_date, end_date, source_url, notes FROM events")]

    races = [dict(r) for r in cur.execute(
        "SELECT id, event_id, race_name, race_number, race_date, status, source_url FROM races")]

    # A season-standings page is not a race. RORC's legacy archive publishes
    # one per class per season alongside the individual race pages, listing
    # every boat that competed with its total points - so its rows repeat boats
    # already counted race by race. In 2022 the IRC Overall standings held 392
    # boats and every single one of them also appears in that season's 16 race
    # pages. Loaded as races they were 251 rows and 12,636 entries, 31% of all
    # RORC entries, each boat counted once more per class-season.
    #
    # They are kept in the database, because the season points and overall
    # positions on them exist nowhere else, and dropped from the export, which
    # is what feeds every count and share figure. 2007-2009 are standings-only
    # - the archive has no per-race pages for those seasons - so excluding
    # them costs those three years their boat-level entries. That is the right
    # trade: a count that is 31% double is worse than a gap that is visible on
    # the Coverage page.
    # Only where the per-race pages exist to duplicate. 2007-2009 have no race
    # pages in the archive at all - the season index lists 8 to 10 links and
    # every one is a standings page - so there the standings ARE the record,
    # and dropping them would take 435 boats and three seasons of fleet out of
    # the dashboard to fix a double-count that is not happening. Decided per
    # (event, class), because the redundancy is per class: a class whose race
    # pages 404ed keeps its standings even in a season where other classes
    # have both.
    standings = [r for r in races if r["race_name"] == "Season Standings"]
    standings_race_ids = set()
    if standings:
        cls_by_race = {}
        for rid, cl in cur.execute(
                "SELECT race_id, class FROM race_entries GROUP BY race_id, class"):
            cls_by_race.setdefault(rid, set()).add(cl)
        covered = set()          # (event_id, class) that a real race already holds
        for r in races:
            if r["race_name"] == "Season Standings":
                continue
            for cl in cls_by_race.get(r["id"], ()):
                covered.add((r["event_id"], cl))
        for r in standings:
            if all((r["event_id"], cl) in covered for cl in cls_by_race.get(r["id"], ())):
                standings_race_ids.add(r["id"])
    races = [r for r in races if r["id"] not in standings_race_ids]
    if standings:
        print(f"  season standings: {len(standings_race_ids)} of {len(standings)} excluded "
              f"(their class already has real races in that event); "
              f"{len(standings) - len(standings_race_ids)} kept as the only record")

    class_counts = [dict(r) for r in cur.execute(
        "SELECT event_id, class_label, entry_count, source FROM event_class_counts")]

    boats_rows = cur.execute("""
        SELECT b.id, b.sail_no, b.boat_name, b.boat_type, b.tcc,
               o.name AS owner_name,
               bc.lead_rep, bc.contacted_by, bc.boat_captain, bc.programme_manager,
               bc.in_cs, bc.tag, bc.notes AS crm_notes
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
    # Their races were dropped above; drop their entries too, or every count
    # keeps the duplication and the client is left holding entries whose
    # race_id resolves to nothing.
    if standings_race_ids:
        before = len(entries_rows)
        entries_rows = [e for e in entries_rows if e["race_id"] not in standings_race_ids]
        print(f"  and {before - len(entries_rows)} entr(y/ies) on those races")

    # ---- one boat, one race: drop the aggregate class rows -----------------
    # RORC and JOG both score a race several times over. RORC publishes a race
    # once per IRC division AND once as "IRC Overall"; JOG scores its numbered
    # classes and then again as Double Handed, Generation JOG and the Women's
    # Sailing Series. race_entries is UNIQUE(race_id, boat_id), so this is
    # invisible at the row level - the duplication is across the class-specific
    # race ROWS of one real race, keyed on (event, race_name).
    #
    # Measured before deciding. Of 39 RORC races holding both an Overall row
    # and class rows, all 39 share boats, and the Overall set is usually a
    # subset: the 2019 Fastnet has 333 boats on Overall and every one of them
    # in a class row. On the JOG side the overlap is total - every Double
    # Handed, Generation JOG and Women's boat in Cowes-Cherbourg 2026 and
    # TeamO Cherbourg 2025 is also in a numbered class, 100% of them. That is
    # 15,972 IRC Overall entries against 18,408 in real divisions, and 1,670
    # JOG overlay entries against 3,022: about 15% of the database counted
    # twice.
    #
    # An explicit list rather than a rule, because the obvious rule does not
    # work. "How often does this class share boats with another" is symmetric -
    # IRC 2 scores 56% only BECAUSE IRC Overall sits on top of it - so it
    # cannot say which of a pair is the re-cut. "Fewer boats wins" is wrong
    # too: Double Handed is smaller than Class 2 and is still the overlay.
    #
    # Kept where it is the only record: an aggregate row is dropped only for a
    # boat that also appears under a real division in the same race, so a
    # two-handed-only race, or the RORC races that publish nothing but an
    # Overall, keep every entry they have.
    AGGREGATE_CLASSES = {
        "IRC Overall", "ORC Overall",          # whole-fleet aggregates
        "ORC 1", "ORC 2", "ORC Two-Handed",    # the same boats under a second rating system
        "IRC Two-Handed",                      # a category overlay on the divisions
        "Double Handed", "Generation JOG", "Women's Sailing Series",
        "One Design",
    }
    # A preference order, not a yes/no. A binary "is this an aggregate" test
    # cannot choose between two aggregates - a boat in an ORC-only race sits in
    # both "ORC 2" and "ORC Overall", neither of them a real IRC division - and
    # it treats a NULL class as a division, so a boat with a blank row and an
    # "IRC 3" row keeps both. Both cases left 667 boats still counted twice.
    # Ranking them and keeping the best one per boat per race collapses all of
    # it, and says plainly what the rule is: a boat counts once in a race, and
    # the row that survives is the most specific one available.
    WHOLE_FLEET = {"IRC Overall", "ORC Overall"}

    def specificity(cls):
        cls = cls or ""
        if not cls:
            return 3                      # no division recorded at all
        if cls in WHOLE_FLEET:
            return 2                      # the whole fleet in one table
        if cls in AGGREGATE_CLASSES:
            return 1                      # a category or a second rating system
        return 0                          # a real division

    race_key = {r["id"]: (r["event_id"], r["race_name"] or "") for r in races}
    had_entries_before = {e["race_id"] for e in entries_rows}
    best = {}
    for i, e in enumerate(entries_rows):
        if e["race_id"] not in race_key:
            continue
        k = (*race_key[e["race_id"]], e["boat_id"])
        rank = specificity(e["class"])
        if k not in best or rank < best[k][0]:
            best[k] = (rank, i)
    winners = {i for _, i in best.values()}
    kept_entries, agg_dropped = [], 0
    for i, e in enumerate(entries_rows):
        if e["race_id"] in race_key and i not in winners:
            agg_dropped += 1
            continue
        kept_entries.append(e)
    entries_rows = kept_entries
    if agg_dropped:
        # Races left holding nothing go too, or the tree grows an "IRC Overall"
        # branch with no boats under it.
        still_used = {e["race_id"] for e in entries_rows}
        emptied = had_entries_before - still_used
        races = [r for r in races if r["id"] not in emptied]
        print(f"  one boat one race: dropped {agg_dropped} duplicate entr(y/ies) where the "
              f"same boat was scored again under an aggregate class (IRC/ORC Overall, "
              f"Double Handed, Generation JOG...), keeping the most specific division; "
              f"{len(emptied)} race row(s) left holding nothing")

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
    regatta_by_id = {r["id"]: r for r in regattas}
    # Boat types that are pure one-design fleets. A TCC on its own does NOT
    # qualify one of these: a handful of SB20s and J/70s carry a stray rating
    # yet have never started an IRC race, and they were leaking through.
    # Hulls that race ONLY as a one-design. A class comes off this list the
    # moment it is shown to race under IRC as well - the boat then enters the
    # dataset, while the entry-level filter below still drops its one-design
    # starts, so only its IRC racing counts.
    #
    # Removed after checking how each class actually races: Contessa 32
    # (114 IRC-division starts), J/80 (63 of its 100 classed entries), Cork
    # 1720 (15 of 48), Etchells (9 of 46), XOD (19 of 530), Daring (4 of 48),
    # J/70 (12 of 1,640), RS 21 (2 of 15). The last four race IRC only
    # occasionally, but occasionally is not never, and dropping the hull threw
    # those starts away along with the boat.
    OD_TYPE_RE = re.compile(
        r"^\s*(sb\s?20|squib|sunbeam|dragon|"
        r"sonar|mermaid|redwing|victory|flying\s?15|swallow|"
        r"rs\s?elite|sonata|folkboat)\b", re.I)

    # Some organisations race entirely under IRC but never write the word in a
    # class label: JOG's divisions are "Class 1", "Double Handed", "Generation
    # JOG". Requiring the word dropped the entire JOG fleet, so for those
    # organisers the event itself is the IRC signal.
    IRC_BY_ORGANISER = {"JOG", "RORC"}
    # The same problem reaches one club event. Round the Island splits its whole
    # fleet into IRC 0-3 and writes them as bare digits ("0", "1"), which is how
    # those divisions are stored in event_class_counts too. Requiring the word
    # dropped all 551 of its boat-level entries while leaving the aggregate
    # counts standing, so the biggest fleet in the Solent read as "no named
    # boats" at the exact moment we finally had every one of them. Matched on
    # name rather than category because its category is "Club", which covers a
    # lot of racing that genuinely is not IRC.
    IRC_BY_NAME_RE = re.compile(r"^Round the Island", re.I)

    def irc_organiser(reg):
        return bool(reg) and (reg.get("category") in IRC_BY_ORGANISER
                              or IRC_BY_NAME_RE.match(reg.get("name") or ""))

    irc_event_ids = {e["id"] for e in events
                     if irc_organiser(regatta_by_id.get(e["regatta_id"]))}
    irc_race_ids = {r["id"] for r in races if r["event_id"] in irc_event_ids}

    # First clause: actually started an IRC race - the strongest possible signal.
    irc_boat_ids = {e["boat_id"] for e in entries_rows
                    if (e["class"] and irc_class_re.search(e["class"]))
                    or e["race_id"] in irc_race_ids}
    # Second clause: holds an IRC rating and isn't a one-design. This rescues
    # boats whose entries carry no class label at all (~13k rows, mostly RORC),
    # which would otherwise be dropped despite plainly being IRC boats.
    # ...and the rating has to look like an IRC TCC. Some sources put a
    # Portsmouth Yardstick number in this column instead (Folkboats carrying
    # "1081"), which was qualifying a whole one-design fleet as IRC. Real TCCs
    # run ~0.7-1.4 for monohulls, up to ~2.25 for MOD70s and super-maxis.
    def is_irc_tcc(v):
        try:
            return 0.6 <= float(v) <= 3.0
        except (TypeError, ValueError):
            return False

    irc_boat_ids |= {b["id"] for b in boats_rows
                     if is_irc_tcc(b["tcc"]) and not OD_TYPE_RE.match(b["boat_type"] or "")}
    # Override: a one-design hull is not an IRC boat even when it has started an
    # IRC race. A handful do (an XOD with 28 "IRC Class 7" starts at Cowes, a
    # J/70 with 11) - those entries are real, but the fleet is not one we rate
    # or sell IRC sails into, and this tool is scoped to the IRC fleet. Six
    # boats; reverse by dropping this line and re-running the export.
    irc_boat_ids -= {b["id"] for b in boats_rows if OD_TYPE_RE.match(b["boat_type"] or "")}

    n_boats_all = len(boats_rows)
    boats_rows = [b for b in boats_rows if b["id"] in irc_boat_ids]
    entries_rows = [e for e in entries_rows if e["boat_id"] in irc_boat_ids]

    # ---- entry-level: keep only racing that actually happened under IRC ----
    # Being an IRC boat isn't enough for the ENTRY to count. Cowes Week alone
    # publishes 85 "classes", of which only IRC Class 0-7 are IRC divisions;
    # the rest are one-design fleets, cruiser/club-handicap divisions (Club
    # Cruiser, Performance Cruiser, Sunsail, Sportsboat) that aren't IRC-rated,
    # and trophies.
    #
    # Trophies (Britannia Cup, Queen's Cup, NYYC Challenge Cup, Triple Crown)
    # are not classes at all - they're prizes contested BY the IRC divisions,
    # so those entries are real IRC racing wearing the wrong label. Where the
    # boat also raced a named IRC division at the same regatta that season the
    # entry is relabelled to it; where it didn't, it's kept as IRC racing under
    # a single bucket rather than inventing a class per trophy.
    # "IRC" need not start the label: Hamble writes "HWS IRC1" and "Autumn
    # Regatta IRC2", Cowes writes "LMIS IRC Class 3". Anchoring this to the
    # start silently dropped every Hamble entry, so it searches instead.
    IRC_DIVISION_RE = re.compile(
        r"\bIRC\s*(?:class\s*)?"
        r"(?:[0-7][ab]?\b|zero|one\b|two\b|three|four|five|six|seven|"
        r"overall|super\s*zero|sz\b|canting\s*keel|ck\b|two[- ]handed|2h\b|irm\b)", re.I)
    TROPHY_RE = re.compile(
        r"^(britannia cup|queen'?s cup|new york yacht club challenge cup|"
        r"triple crown.*|double[- ]handed)$", re.I)

    def norm_div(s):
        return re.sub(r"\s+", " ", (s or "").strip())

    # boat + regatta-season -> the IRC divisions it raced there
    race_event = {r["id"]: r["event_id"] for r in races}
    event_key = {e["id"]: (e["regatta_id"], e["season_year"]) for e in events}
    div_by_boat_event = {}
    for e in entries_rows:
        cl = norm_div(e["class"])
        if cl and IRC_DIVISION_RE.search(cl):
            ek = event_key.get(race_event.get(e["race_id"]))
            if ek:
                div_by_boat_event.setdefault((e["boat_id"], ek), set()).add(cl)

    kept, relabelled, trophy_kept, n_entries_all = [], 0, 0, len(entries_rows)
    for e in entries_rows:
        cl = norm_div(e["class"])
        if not cl:
            kept.append(e)                      # unlabelled: mostly RORC IRC racing
            continue
        # JOG's divisions are "Class 1", "Double Handed", "Generation JOG" - it
        # races only under IRC but never writes the word, so requiring it here
        # threw away 4,200 of its 5,000 entries.
        if e["race_id"] in irc_race_ids:
            kept.append(e)
            continue
        if IRC_DIVISION_RE.search(cl):
            kept.append(e)
            continue
        if TROPHY_RE.match(cl):
            ek = event_key.get(race_event.get(e["race_id"]))
            divs = div_by_boat_event.get((e["boat_id"], ek))
            d = dict(e)
            if divs and len(divs) == 1:
                d["class"] = next(iter(divs))
                relabelled += 1
            else:
                d["class"] = "IRC (division unrecorded)"
                trophy_kept += 1
            kept.append(d)
            continue
        # everything else - one-design fleets, cruiser and club-handicap
        # divisions - is not IRC racing and is dropped.
    entries_rows = kept
    n_entries_irc = len(entries_rows)

    # a boat left with no IRC racing at all is no longer in scope
    still = {e["boat_id"] for e in entries_rows}
    boats_rows = [b for b in boats_rows if b["id"] in still]

    # Aggregate class-count rows are labelled far more tersely ("1", "2", "0"
    # are IRC divisions), so only the explicitly one-design fleets are dropped.
    # J/111 and J/109 appear both as bare one-design labels and as "(IRC)"
    # variants - only the plain ones are dropped, since the parenthesised ones
    # are explicitly the IRC-rated split of that fleet.
    OD_CLASS_LABELS = {"j/70", "j70", "sb20", "sb 20", "xod", "x one design",
                       "squib", "sunbeam", "dragon", "etchells", "daring",
                       "sonar", "mermaid", "redwing", "victory", "flying 15",
                       "contessa 32", "sonata", "swallow", "rs elite", "j/80",
                       "j111", "j/111", "j109", "j/109"}
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
          f"{n_entries_irc}/{n_entries_all} entries "
          f"({relabelled} trophy entries relabelled to their IRC division, "
          f"{trophy_kept} kept as 'division unrecorded'); "
          f"recomputed totals for {len(dropped_cc)} event(s)")

    sm_history_rows = cur.execute("""
        SELECT boat_id, sailmaker_id, effective_from, effective_to, source, confidence
        FROM boat_sailmaker_history ORDER BY boat_id, id
    """).fetchall()

    name_history_rows = cur.execute("""
        SELECT boat_id, name, first_season, last_season, variants, confidence
        FROM boat_name_history ORDER BY boat_id, first_season
    """).fetchall()

    owner_history_rows = cur.execute("""
        SELECT boat_id, owner_id, effective_from, effective_to, source, confidence, is_charter
        FROM boat_owner_history ORDER BY boat_id, id
    """).fetchall()

    # ---- assemble boats with nested entries + sailmaker history ----
    races_by_id = {r["id"]: r for r in races}
    events_by_id = {e["id"]: e for e in events}

    def entry_season_year(entry):
        race = races_by_id.get(entry["race_id"])
        ev = events_by_id.get(race["event_id"]) if race else None
        return ev["season_year"] if ev else None

    # people + race_crew exist only where a source published skipper names
    # (JOG and the RORC per-race pages so far), so this is deliberately small.
    try:
        crew_rows = [dict(r) for r in cur.execute(
            "SELECT rc.boat_id, rc.race_id, rc.role, p.name AS person "
            "FROM race_crew rc JOIN people p ON p.id = rc.person_id")]
    except sqlite3.OperationalError:
        crew_rows = []          # tables not created yet on an older database
    crew_rows = [c for c in crew_rows if c["boat_id"] in irc_boat_ids]

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

    namehist_by_boat = {}
    for r in name_history_rows:
        namehist_by_boat.setdefault(r["boat_id"], []).append(dict(r))

    ownerhist_by_boat = {}
    for r in owner_history_rows:
        d = dict(r)
        d["owner_name"] = owner_by_id.get(d["owner_id"], {}).get("name") if d["owner_id"] else None
        # Two ways a name on the timeline is not an owner. Some owners ARE
        # charter companies, which the owners table knows. But a person can
        # charter one boat for one season without being a charter operator -
        # Jock Wishart chartering SUNRISE is not the same fact about Jock as it
        # is about Sunsail - so the period carries its own flag, and either is
        # enough.
        d["is_charter"] = (bool(d.pop("is_charter", 0))
                           or bool(owner_by_id.get(d["owner_id"], {}).get("is_charter_operator"))
                           or None)
        ownerhist_by_boat.setdefault(d["boat_id"], []).append(d)

    boats = []
    for b in boats_rows:
        d = dict(b)
        d["entries"] = sorted(entries_by_boat.get(d["id"], []),
                               key=lambda e: (entry_season_year(e) or 0), reverse=True)
        d["sailmaker_history"] = smhist_by_boat.get(d["id"], [])
        d["owner_history"] = ownerhist_by_boat.get(d["id"], [])
        d["name_history"] = namehist_by_boat.get(d["id"], [])
        # "current" sailmaker = most recent history row, else most recent entry's sailmaker
        current_sm = None
        # A PARTIAL inventory is not the boat's sailmaker. GLADIATOR is a
        # Quantum boat carrying some North; taking the most recent history row
        # regardless of confidence made it read as a North boat, which
        # overstates our share by exactly the boats we have only half of.
        main_hist = [h for h in d["sailmaker_history"] if h.get("confidence") != "partial"]
        if main_hist:
            current_sm = main_hist[-1]["sailmaker_name"]
        elif d["entries"]:
            for e in d["entries"]:
                if e["sailmaker_name"]:
                    current_sm = e["sailmaker_name"]
                    break
        d["current_sailmaker"] = current_sm
        boats.append(d)

    # ---- market share by entries: racing done by boats whose sails we know ----
    # This used to count race_entries.sailmaker_id, which is set by a couple of
    # hand-made loaders and by nothing else - so it measured "entries whose
    # loader happened to record a sailmaker", not "entries sailed by a boat we
    # know the sails of". 345 entries against the 12,619 that the 225 known
    # boats actually sailed, and one collapsed duplicate row could move it by a
    # third, which is what happened when the JOG register was folded into its
    # results.
    #
    # Counted off the boat instead, which is where a sailmaker is actually
    # recorded. Weighting by entries is the point of having this chart beside
    # the per-boat one: a boat that sails twenty races is twenty races' worth of
    # sails in the fleet, and a boat that sails one is not.
    #
    # Uses the boat's CURRENT sailmaker for every season it raced. That is exact
    # for all but 14 boats - the only ones in the database with more than one
    # maker on record - and of 282 sailmaker rows just 62 carry a start date, so
    # attributing by season would be guesswork dressed as precision for the sake
    # of those 14.
    from collections import Counter, defaultdict
    entries_per_boat = Counter(e["boat_id"] for e in entries_rows)
    sm_counts = Counter()
    for b in boats:
        if b["current_sailmaker"]:
            sm_counts[b["current_sailmaker"]] += entries_per_boat.get(b["id"], 0)
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
        # how many entries carry any sailmaker at all - the honest
        # denominator behind every market-share figure in the dashboard
        "n_entries_with_sailmaker": sum(1 for e in entries_rows if e["sailmaker_id"]),
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
        # Crew, for the Analysis tab. Only the boats that survived the IRC
        # filter, so the panel cannot show people sailing boats the rest of the
        # dashboard has excluded.
        "crew": crew_rows,
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
        b["name_history"] = [strip_nulls(h) for h in b.get("name_history", [])]

    with open(out_path, "w") as f:
        json.dump(data, f, default=str)
    print(f"Wrote {out_path} ({len(boats)} boats, {len(entries_rows)} entries, {len(trend_rows)} trend rows)")

if __name__ == "__main__":
    main()
