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
  - the export/build are pure functions of the database

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

    # A re-run mints fresh race rows for anything already loaded; collapse them
    # before the export so counts never drift upward on repeat runs.
    ok, _ = run("Deduplicate races", ["dedupe_races.py"], args.db, args.timeout)
    if not ok:
        failures.append("dedupe")

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
