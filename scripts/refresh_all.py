#!/usr/bin/env python3
"""
One command that refreshes the whole database and rebuilds the dashboard.

This is the piece that turns a pile of scrapers into something that collects
results on its own. Run it on a schedule (Task Scheduler / cron) and the
dashboard keeps itself current.

    python3 scripts/refresh_all.py db/marketshare.db

Every step is idempotent, which is what makes unattended running safe:
  - loaders use INSERT OR REPLACE on UNIQUE(race_id, boat_id)
  - dedupe_races.py collapses any duplicate race rows a re-run creates
  - the correction ledgers in data/ report no change on a second pass
  - the export/build are pure functions of the database

Two kinds of step run after the scrape, and PIPELINE holds both: those that
re-DERIVE something from the entries (owners, boat types, naming history), and
those that re-APPLY a decision a person made (merges, splits, class spellings,
owner corrections). Both have to be here, because a scrape undoes both kinds:
it re-imports the wrong owner off the results page, and it re-fragments the
labels the decisions were made about.

apply_sailscan_matches.py is deliberately NOT in the pipeline. Nothing in a
refresh re-derives sailmaker history, so there is nothing to undo its work,
and re-running it every week would only re-assert what is already there.

Sources are declared in SOURCES below. Each entry says how to invoke a scraper
for the current season; add a club or a regatta by adding a row, not by editing
the runner. Sources that need a human (JOG, RORC 2023+) are listed in BLOCKED
so the summary keeps reminding you they are missing rather than quietly
pretending the picture is complete.

Exit code is non-zero if any source failed, so a scheduler can alert on it.
"""
import sys
import time
import argparse
import subprocess
import datetime
import sqlite3

SCRIPT_DIR = __import__("pathlib").Path(__file__).resolve().parent
PY = sys.executable

# name -> argv after the db path. Kept to sources that publish a live current
# season; historical backfills are one-off and not re-run here.
SOURCES = [
    ("Cowes Week",            ["scrape_cowes_week.py", "{year}", "--delay", "3"]),
    ("Royal Southern",        ["scrape_royal_southern.py", "--delay", "4"]),
    ("Warsash Spring",        ["scrape_warsash.py", "--year", "{year}", "--delay", "4"]),
    ("Hamble (HalSail)",      ["scrape_hamble.py", "--club", "3560", "--delay", "4"]),
    ("Royal Solent (HalSail)", ["scrape_hamble.py", "--club", "3488",
                                "--regatta", "Taittinger Royal Solent Regatta", "--delay", "4"]),
]

# Sources we cannot collect automatically, and why. Surfaced every run.
BLOCKED = [
    ("JOG", "myjog.jog.org.uk robots.txt disallows ClaudeBot (Cloudflare managed "
            "AI-crawler list). Needs JOG to allow it, or a member-side export."),
    ("RORC 2023+", "rorc.org results moved to sailracehq.com, whose robots.txt "
                   "disallows ClaudeBot. Legacy archive covers 2007-2022 only."),
    ("Royal Thames", "The 250th Anniversary Regatta's IRC results are on "
                     "rtyc.nautical-cloud.com and racing.royalthames.com; both disallow "
                     "ClaudeBot (same Cloudflare AI-crawler list as JOG). The non-IRC half "
                     "IS fetchable - filedn.com/.../RTYC/250thResults.html, no robots.txt - "
                     "but it is 12 one-design classes (RS Elite, Etchells, Dragon, SB20...) "
                     "that the IRC scope excludes anyway. The Annual Regatta has no single "
                     "source: royalthames.com/results scatters across KSail, YachtScoring "
                     "and the Cape 31 class CMS, and the two YachtScoring events it links "
                     "serve no result tables."),
]


def run(step, argv, db, timeout):
    print(f"\n=== {step} ===", flush=True)
    cmd = [PY, str(SCRIPT_DIR / argv[0]), db] + argv[1:]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, timeout=timeout, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        print(f"  TIMED OUT after {timeout}s", flush=True)
        return False, 0
    tail = [l for l in (r.stdout or "").splitlines() if l.strip()][-3:]
    for l in tail:
        print("  " + l[:150], flush=True)
    if r.returncode != 0:
        print(f"  FAILED rc={r.returncode}: {(r.stderr or '').strip()[:300]}", flush=True)
    print(f"  ({time.time()-t0:.0f}s)", flush=True)
    return r.returncode == 0, time.time() - t0


def counts(db):
    c = sqlite3.connect(db).cursor()
    return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("boats", "race_entries", "races", "regattas")}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--year", type=int, default=datetime.date.today().year)
    p.add_argument("--timeout", type=int, default=3600, help="per-source seconds")
    p.add_argument("--skip-scrape", action="store_true",
                   help="only dedupe, export and rebuild")
    p.add_argument("--only", help="run just this source (substring match)")
    args = p.parse_args()

    started = datetime.datetime.now()
    print(f"Refresh started {started:%Y-%m-%d %H:%M}  season {args.year}")
    before = counts(args.db)

    failures = []
    if not args.skip_scrape:
        for name, argv in SOURCES:
            if args.only and args.only.lower() not in name.lower():
                continue
            argv = [a.format(year=args.year) for a in argv]
            ok, _ = run(name, argv, args.db, args.timeout)
            if not ok:
                failures.append(name)

    # Everything below either re-derives something from the entries, or
    # re-applies a decision a person made that a fresh scrape would otherwise
    # undo. The order is load-bearing; each comment says why that step sits
    # where it does.
    #
    # Half of these were missing until 2026-09-07, and two of them
    # (normalise_classes, apply_owner_corrections) have docstrings promising
    # they are "re-applied on every run" - a guarantee that was only ever true
    # if somebody remembered to type the command. A correction that is not in
    # this list is a correction with a shelf life of one scrape.
    PIPELINE = [
        # Before anything that matches on a name. The scrape re-imports the
        # same cp1252-mangled French owners every week, and a mangled
        # boat_name_used will not match a MoveNames or OnlyNamed value.
        ("Repair mojibake", ["repair_mojibake.py"], "mojibake"),

        # Regatta-level folds first: merging two records for one real event
        # creates races that now exist twice, which the dedupe below collapses.
        # Before the merges: this creates the regattas the sponsor-variant folds
        # below then act on, and it is what stops every RORC race in the tree
        # showing three seasons when the database holds fifteen.
        ("Promote RORC races out of season buckets",
         ["promote_rorc_races.py", "--file", "data/rorc_race_regattas.csv"], "rorc promote"),
        # Immediately before the merges, because a rename is what CREATES the
        # correctly-named record a merge row then folds duplicates into. Where
        # every record of a series carries a year - "2025 J70 European
        # Championship" against "J70 European Championship 2026 Barcelona" -
        # there is no keeper until one of them is renamed, so run the other way
        # round the merge has nothing to aim at.
        ("Rename regattas",
         ["rename_regattas.py", "--file", "data/regatta_renames.csv"], "regatta renames"),
        ("Merge regattas", ["merge_regattas.py", "--file", "data/regatta_merges.csv"], "regatta merges"),

        # After the renames and merges, so a fixture can be attached to the
        # record those steps settle on rather than minting a second one beside
        # it. Fixtures are regattas that have not been sailed yet - dates and
        # nothing else - and a re-import never creates them, so without this
        # they would exist only until someone rebuilt the database from source.
        ("Add fixtures", ["add_fixtures.py", "--file", "data/fixtures.csv"], "fixtures"),

        # Class labels decide race identity - races are keyed on (event, race
        # name, class) - so canonicalising them can make two race rows
        # identical. That is why this runs BEFORE the dedupe rather than after.
        ("Canonicalise classes", ["normalise_classes.py"], "classes"),

        # A re-run mints fresh race rows for anything already loaded; collapse
        # them before the export so counts never drift upward on repeat runs.
        ("Deduplicate races", ["dedupe_races.py"], "dedupe"),

        # Boat-level merges, then the splits that undo the wrong ones. Both are
        # ledgers of human decisions and both are idempotent: a second pass
        # reports no change. Re-running is also what repairs a record left
        # wearing the wrong boat's name, and what pulls back entries a
        # re-scrape has re-filed onto the wrong hull.
        ("Merge boats", ["merge_boats.py", "--file", "data/boat_merges.csv"], "boat merges"),
        ("Split wrongly-merged boats", ["split_boat.py", "data/boat_splits.csv"], "boat splits"),

        # Before everything that reasons about boats, because a copy of an
        # entry list makes one boat look like three and sends the duplicate
        # report after records that are not duplicates. The UK IRC Nationals
        # 2025 list is inside the Moth Worlds and 470 Worlds workbooks as
        # well as its own.
        # A spreadsheet division heading imported as a boat. Before the boat
        # reasoning for the same reason as the row below: a subtotal in the
        # fleet is a phantom record the duplicate report then asks about.
        ("Drop spreadsheet heading rows", ["drop_heading_rows.py"], "heading rows"),

        ("Drop misfiled entry lists",
         ["drop_misfiled_entry_lists.py", "--file", "data/misfiled_entry_lists.csv"],
         "misfiled entry lists"),

        # Before the split below, which reads boat_type_used as evidence of
        # what a boat IS. One import slipped a column and wrote the owner's
        # home town there, and a town that looks like a type is a town that
        # proves two boats are different when they are the same one.
        ("Drop column-shifted entries", ["drop_column_shifted_entries.py", "--apply"], "shifted"),

        # A bare sail number is not an identity, so every boat that ever raced
        # under "45" anywhere became one record. This takes them apart, and it
        # belongs here: after the merges have settled which records exist, and
        # before repair_boat_names, backfill_owners, backfill_boat_names,
        # normalise_boat_types and refresh_tcc, which between them re-derive
        # the name, owner, type and rating of every record it creates.
        ("Split shared sail numbers", ["split_shared_sail_numbers.py", "--apply"], "sail splits"),

        # Straight after those two, because both can leave a record wearing a
        # name whose entry has just moved to another boat. merge_boats repairs
        # the pairs in its own ledger; this catches every other case, including
        # ones left by a merge made before that repair existed - GBR3750 was
        # still called GLASGOW KISS while holding eight GOOD HYDEING entries,
        # the real GLASGOW KISS being SGP3750. Records that raced under more
        # than one name are reported and left alone.
        ("Repair stale boat names", ["repair_boat_names.py"], "boat names stale"),

        # After the regatta merges above, which is what brings an entry list and
        # the results that replaced it into one place. An entry list is loaded
        # on purpose for a race not yet sailed; once the results arrive it is a
        # duplicate of them. JOG's 2026 Lonely Tower was 93 entered boats from
        # the fleet spreadsheet and 100 from the club's results pages, 91 of
        # them the same boats.
        ("Drop superseded entry lists", ["drop_superseded_entries.py"], "superseded entries"),

        # Loaders write the owner onto the ENTRY; this promotes it to the boat
        # record the dashboard actually reads. Skipping it left 2,729 boats
        # looking ownerless while their own race history named the owner, so it
        # belongs in every run rather than being remembered occasionally.
        # Before the backfill below, which builds a boat's ownership TIMELINE.
        # Two spellings of one person make two segments and a boat reads as
        # sold and bought back - 599 dashboard boats showed that, a third of
        # them for no other reason.
        ("Fold owner spellings", ["merge_owner_spellings.py", "--apply"], "owner spellings"),

        ("Backfill owners", ["backfill_owners.py"], "owners"),

        # Strictly after the backfill above, which re-derives ownership from
        # whoever the results page printed - the very thing these corrections
        # exist to override. Run in the other order, every correction is undone
        # by the step that follows it.
        ("Apply owner corrections", ["apply_owner_corrections.py"], "owner corrections"),

        # Scrapers write whatever spelling their source uses, so every run
        # re-fragments the type list (J/109, J 109, J109 as three fleets). This
        # has to run after the scrape or the canonical names last exactly one
        # week.
        # Before the canonicaliser, so a type recovered from the entries gets
        # the same spelling treatment as one that was already there.
        # 527 boats carried "-" or "None" as a type while their own entries
        # named one dozens of times - SOAK THERAPY an IMOCA 60 on 56 of them.
        # Only fills where the entries agree; 189 that disagree are reported
        # and left, because those are shared sail numbers and column shifts
        # rather than a spelling to pick.
        ("Backfill boat types", ["backfill_boat_types.py", "--apply"], "types recovered"),
        ("Canonicalise boat types", ["normalise_boat_types.py"], "boat types"),

        # Naming history is derived from the names boats raced under, so it has
        # to be rebuilt after each scrape like the owner history is.
        ("Derive naming history", ["backfill_boat_names.py"], "boat names"),

        # Every scraper writes dates in whatever its source used, so this has
        # the same shelf life as the class labels: one run. It also derives the
        # event start/end span from the races underneath, which is what puts a
        # regatta on the calendar at all.
        ("Normalise dates", ["normalise_dates.py"], "dates"),
        # After normalise_dates, never before: a published fixture date is
        # better evidence than a span derived from whichever races happened
        # to carry one, so the ledger has to be the last word.
        ("Apply published fixture dates",
         ["apply_event_dates.py", "--file", "data/event_dates.csv"], "event dates"),

        # After every loader and every merge, so the entries it reads are
        # final. A boat's IRC rating is a fact about the boat IN A SEASON -
        # new sails move it every year - so the boat record has to be
        # re-derived from the last rating the boat actually raced under,
        # the same way owners are re-derived above.
        ("Refresh IRC TCCs", ["refresh_tcc.py", "--apply"], "tcc"),

        # After the derived ratings above, because a certificate beats a
        # results page: one is what the Rating Office issued, the other is
        # whatever the organiser typed. refresh_tcc exists to fill the gap
        # where no certificate is held, not to compete with one.
        ("Load IRC certificates",
         ["load_irc_tcc_listing.py", "data/irc_tcc_certificates.csv", "--apply"], "irc certs"),

        # Strictly AFTER the TCC refresh above. Both can set a boat's rating
        # and only one of them is a person: a TCC typed into the dashboard is
        # a decision, the derived one is an inference from the last result,
        # and the decision has to be able to overrule it. Run the other way
        # round, every manual rating is silently undone each week.
        ("Apply boat corrections",
         ["apply_boat_updates.py", "data/boat_updates.csv"], "boat updates"),

        # Last, so it gets the last word. Every segment in the database is
        # either an importer's guess from the regatta's name or a human
        # decision in data/segment_overrides.csv, and this step is what makes
        # the second kind survive the first. It was missing from this list
        # entirely, which meant a correction lasted exactly until the next
        # rebuild - the one failure the whole ledger pattern exists to prevent.
        ("Apply segment overrides", ["apply_segment_overrides.py", "--apply"], "segments"),

        # After the segments, and after anything that can set a venue: the
        # area is read off the venue, so it has to be the last word on
        # where a regatta is. Ninety-five venues is too fine to ask "how
        # are we doing on the South Coast" of, which is what the area is
        # for - and regattas.region cannot answer it, because it records
        # whose race it is rather than where.
        ("Apply venue areas", ["apply_venue_areas.py", "--apply"], "areas"),

        # Straight after the areas, because it reads them. A regatta's country
        # is the country of its VENUE, and for a point-to-point race the venue
        # is the port it FINISHES in - so Cowes to Cherbourg was France and
        # fell out of the United Kingdom altogether, eleven races and 6,213
        # entries of a mostly British fleet. This writes the area's home market
        # onto the regatta as market_country, which is what the dashboard
        # scopes and filters on.
        ("Apply area countries", ["apply_area_countries.py", "--apply"], "market countries"),

        # Researched sailmaker findings, each with the page it came from. They go to
        # boat_sailmaker_history, not onto the boat, so the dashboard derives the
        # current maker from them and deleting a row undoes it. Last, because a
        # scrape re-asserts whatever the results pages said and a researched finding
        # has to outrank that - race results carry a sailmaker on 151 entries out of
        # 68,553, so almost everything we know came from somewhere else.
        ("Load sailmaker research",
         ["load_sailmaker_research.py", "data/sailmaker_research.csv"], "sailmaker research"),
    ]
    for label, argv, tag in PIPELINE:
        ok, _ = run(label, argv, args.db, args.timeout)
        if not ok:
            failures.append(tag)

    for step, argv in (("Export JSON", ["export_dashboard_data.py", "dashboard/data.json"]),
                       ("Build dashboard", ["build_dashboard.py"])):
        # build_dashboard takes no db argument
        cmd = [PY, str(SCRIPT_DIR / argv[0])]
        if argv[0] != "build_dashboard.py":
            cmd += [args.db] + argv[1:]
        print(f"\n=== {step} ===", flush=True)
        r = subprocess.run(cmd, capture_output=True, text=True)
        for l in (r.stdout or "").splitlines()[-2:]:
            print("  " + l[:150], flush=True)
        if r.returncode != 0:
            print(f"  FAILED: {(r.stderr or '').strip()[:300]}", flush=True)
            failures.append(step)

    after = counts(args.db)
    print("\n=== summary ===")
    for k in before:
        d = after[k] - before[k]
        print(f"  {k:14} {before[k]:>7} -> {after[k]:>7}  ({d:+d})")
    print("\n  not collectable automatically:")
    for name, why in BLOCKED:
        print(f"    {name}: {why}")
    took = (datetime.datetime.now() - started).total_seconds() / 60
    if failures:
        print(f"\n  {len(failures)} step(s) FAILED: {', '.join(failures)}  [{took:.0f} min]")
        sys.exit(1)
    print(f"\n  all steps OK  [{took:.0f} min]")


if __name__ == "__main__":
    main()
