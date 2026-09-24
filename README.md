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

## Loading an event's entry list

Open the dashboard, go to **Entry list**, and drop the organiser's spreadsheet on
the page. Everything up to the last step happens in the browser and writes
nothing.

The page identifies which event the list belongs to, using four independent
signals and showing you each one it used:

| signal | where it comes from |
| --- | --- |
| regatta name | the filename, with brackets, years and "marketshare" stripped |
| season | the sheet tab, which in these workbooks is usually the year |
| dates | the date range in the filename's brackets, e.g. `[27th Sept - 5th October]` |
| fleet | how many of the matched boats have raced that regatta before |

Name matching weights rare words over common ones, so "Les Voiles de
Saint-Tropez" is not confused with "Les Voiles d'Antibes" despite sharing three
words out of five. Across the 379-workbook corpus this identifies 84% of files
outright; the rest open an **event finder** (search by name, filter by month,
location and season) or, if the event is genuinely new, a card that writes the
`load_marketshare_csv.py` command for you.

It deliberately falls through to the finder rather than guessing when two
regattas match equally well - which is usually a sign the database holds the
same regatta twice, and worth fixing before loading anything into either copy.

Picking an event produces the command that does the write. Run it from the
project directory:

```
python scripts/load_entry_list.py db/marketshare.db "<the file>" \
  --regatta "Les Voiles de Saint-Tropez" --year 2026 --sheet "2026" --dry-run
```

`--sheet` matters: these workbooks routinely carry one tab per season, and
without it the loader reads whichever tab is leftmost. Drop `--dry-run` to
write. The sheet's own Sailmaker column is reported but **not** imported unless
you add `--import-sailmakers` - it sometimes disagrees with what we hold, and
silently overwriting a maker moves the market-share numbers.

Then rebuild (the three commands above) and the event appears in the dashboard.

## How "most successful boats" is measured

A finishing position means nothing without the size of the fleet it was scored
in. Until this was fixed, the Analysis page ranked boats on podium rate, and
THE BODFATHER - a mid-fleet Cape 31 whose finishes against real fleets run
4, 2, 12, 6, 13, 6, 10, 19, 16, 9, 13, 6, 10, 18, 24, 5, 19, 17 - came top on
six 1st places, every one of them in a race where it was the only entry.
100 of the 2,509 first places in the export were won in a division of one.

So the unit is the **share of the fleet beaten**, `(fleet - position) / (fleet - 1)`:

| column | meaning |
| --- | --- |
| **1sts** | first places, in a fleet of more than one |
| **Win %** | those firsts over the races that count |
| **Fleet beaten** | share of the fleet finished ahead of, averaged over its races |
| **Circuit** | regatta-seasons sailed, out of those its regular rivals turned up to |
| **Rating** | Fleet beaten, pulled toward the 53% fleet average by how little racing there is to judge on. The sort key. |

Three deliberate choices, each made after the obvious version failed on real data:

- **A race is weighted by its fleet, but the weight saturates at 20.** Summing
  boats-beaten straight let one 89-boat pursuit race outweigh forty-three
  one-design races and put a two-race boat third.
- **Rivals must have raced you at least 3 times.** Counting a single shared
  start line made every boat in a mixed IRC fleet a rival and inflated one
  boat's circuit to 161 regatta-seasons.
- **Ratings shrink toward the whole fleet's average, not the selection's.**
  Otherwise filtering to a weak sailmaker would raise everyone's rating.

Fleet size is counted in `export_dashboard_data.py`, after the aggregate-class
dedupe and **before** the IRC filter. It cannot be derived in the browser:
doing so understates 18% of divisions and invents 68 one-boat races out of
divisions that had a real fleet, which flatters every boat in them.

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
