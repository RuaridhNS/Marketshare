# Marketshare — race entry & sailmaker tracking

A boat-centric database + dashboard for monitoring Solent race entries and
North Sails' market share against competitors, built from your two source
spreadsheets (`jog_fleet_combined.xlsx`, `IRC Solent Report.xlsx`) plus a
proof-of-concept live scrape from rorc.org.

## What's here

```
db/schema.sql            - the relational schema (SQLite)
db/marketshare.db         - the database itself (gitignored - rebuild with build_db.py)
scripts/build_db.py       - rebuilds marketshare.db from scratch + imports both spreadsheets
scripts/load_rorc_csv.py  - loads one scraped RORC race (CSV) into the database
scripts/export_dashboard_data.py - exports the DB to dashboard/data.json
scripts/build_dashboard.py       - bakes data.json into the single-file dashboard.html
scripts/README_scraping.md       - the scraping workflow, and what is/isn't scrapable and why
dashboard/dashboard.html  - the actual dashboard - open this in any browser, no server needed
exports/marketshare.sql   - full SQL dump of the database (git-diffable, since the .db binary isn't)
exports/rorc_2021_castlerock_ircoverall.csv - the one real scraped race used as proof of concept
```

## The data model

The **boat** is the central record (`boats` table, keyed on sail number).
Everything else hangs off it: current owner, sailmaker history over time,
CRM fields (lead rep / contacted by / in customer system / tag), and every
race entry it's had — across both regattas and years. A race entry
(`race_entries`) carries the result too (position, corrected time, status)
once the race has been sailed, so the same table doubles as "who's entered
so far" pre-race and "what happened" post-race.

Historical years/regattas where only aggregate class-level counts exist (no
boat-level breakdown) — your IRC Solent Report, 2007-2026 — are kept
separately in `event_class_counts`, linked to `events` (a regatta's given
season) rather than to individual boats.

## Rebuilding after a data change

```
python3 scripts/build_db.py <jog_fleet_combined.xlsx> <IRC_Solent_Report.xlsx> db/marketshare.db
python3 scripts/export_dashboard_data.py db/marketshare.db dashboard/data.json
python3 scripts/build_dashboard.py
```

Third command produces `dashboard/dashboard.html` - the file to open/share.

## Current state & honest limitations

- **147 boats**, **184 boat-level race entries**, **695 aggregate historical
  class-count records** spanning 2007-2026 across 20 regattas/races.
- Boat-level entries come from: your JOG fleet register + 3 JOG entry lists
  (manual, current season — **the JOG sheets had no year recorded, so
  they're filed under 2026 by assumption; correct this if wrong**), plus
  one real scraped RORC race (2021 Castle Rock Race, 32 boats) as a working
  proof of concept for the scraper pipeline.
- **Automated scraping is more limited than originally hoped.** Both
  platforms these results actually live on — MyJOG (all JOG results) and
  SailRaceHQ (current-season RORC results, 2023+) — have opted out of AI
  crawler access in their `robots.txt`, naming Claude's crawler
  specifically. I'm respecting that. The one source that *is* cleanly
  scrapable is RORC's legacy static archive (`rorc.org/raceresults/`,
  covering 2007-2022) — see `scripts/README_scraping.md` for the exact
  workflow and how to extend the backfill season by season.
- Sailmaker tracking (the market-share core) currently only has real
  per-boat signal from the JOG data; the RORC scrape doesn't carry
  sailmaker info (RORC's results pages don't publish it) — RORC entries
  are exactly this: entry/result data, not competitor-tracking data. If it
  is available anywhere (e.g. NOR/sponsor lists), that'd need a different
  source.
- A few regatta names in the IRC Solent Report were reconstructed
  automatically from messy multi-block sheet layouts (e.g. "2H
  Championships", "IRC Nationals" inside the RORC Inshore tab) - worth a
  quick sanity check against the original file if a number looks off.

## Suggested next steps

1. Confirm/correct the JOG season-year assumption (2026) once you know
   which season those entry lists were actually from.
2. Extend the RORC legacy backfill (2007-2022) - mechanical repetition of
   the documented workflow, ideally a season at a time.
3. Ask RORC and/or JOG (given the existing rep relationship) whether an
   official data export/API is available for partner use, since their
   public crawler policy rules out automated scraping of current seasons.
4. Keep feeding new manual entry-list/result exports through
   `build_db.py`/`load_rorc_csv.py` as you get them - the database is
   additive and idempotent, so re-running is always safe.
