# Scraping workflow

## Where this runs (corrected 2026-09-08)

**This section used to say the opposite and was left stale long after it
stopped being true.** It described the project as living in a Claude cloud
sandbox with no general internet access, where "a conventional standalone
`requests`/`BeautifulSoup` scraper cannot run unattended" and everything had to
go through `WebFetch` one page at a time. Anyone reading the top of this file
would have concluded that the way the project actually works is impossible.

It runs on the user's own Windows machine, which has ordinary internet access.
Eight `requests`/`BeautifulSoup` scrapers run there directly, unattended, on a
Task Scheduler job: `refresh_all.py` fetched Cowes Week, the Royal Southern,
Warsash, Hamble and the Royal Solent on the morning of 2026-09-07, 39 minutes
end to end, and rebuilt the dashboard afterwards. Historical backfills run the
same way, a season at a time, by hand.

`WebFetch` is not part of the pipeline. The one place a browser is still needed
is a site that blocks this project's crawler but that the user can read while
signed in - see the Round the Island section below, and
`docs/browser_extraction_prompt.md`.

## Source scrapability audit (checked 2026-08-24)

| Source | robots.txt | Verdict |
|---|---|---|
| `rorc.org` (legacy static results, years **2007-2022**, e.g. `/raceresults/2021/ircoverall11.html`) | `Crawl-delay: 10`, no bot-specific restrictions | **Scrapable.** Plain HTML tables. |
| `sailracehq.com` (current RORC results, **2023-present** — RORC migrated their live-results platform) | `Content-Signal: ai-train=no`; explicitly disallows `ClaudeBot` and other AI crawlers | **Not scraped.** Site has opted out of AI crawler access. |
| `myjog.jog.org.uk` (JOG results, all years) | explicitly disallows `ClaudeBot` and other AI crawlers, cites EU copyright directive | **Not scraped.** Same reason. Continue manual export from this site, or ask JOG for an official data/API arrangement. |
| Cowes Week (`cowesweek.co.uk`) | no robots.txt found (404) | **Built and running.** `scrape_cowes_week.py` (daily results) and `scrape_cowes_points.py` (standings, for the five seasons with no daily results). 65,344 entries - the largest source here. |
| Royal Southern (`scrape_royal_southern.py`) | no restrictions declared | **Built and running.** 3,916 entries. |
| Warsash SC | no restrictions declared | **Built and running.** `scrape_warsash.py` plus `scrape_warsash_pdfs`/`load_warsash_pdfs.py` for the PDF-only summaries. 1,164 entries. |
| Hamble / Royal Solent (HalSail) | no restrictions declared | **Built and running.** `scrape_hamble.py` (by club id) and `scrape_halsail_archive.py`. 1,135 entries. |
| Round the Island (`racing.islandsc.org.uk`) | disallows `ClaudeBot` | **Not scraped.** Read from the user's own signed-in browser; 551 entries, reproducible from `data/rti_islandsc.csv`. See below. |

**Bottom line, as of 2026-09-08:** the claim that "real automation is only
available for the RORC legacy archive" has not been true for a while. Six
clubs and series are scraped unattended on a schedule, and the two blocked
platforms are still blocked:

- **JOG** (`myjog.jog.org.uk`) and **RORC 2023+** (`sailracehq.com`) disallow
  this project's crawler at the platform level. Every run of `refresh_all.py`
  prints both, so the gap stays visible rather than being mistaken for
  completeness. RORC 2023-2025 is partly covered anyway, from season-points
  workbooks exported by hand (3,429 entries).
- Worth asking both for an official export or API arrangement, given the
  existing rep relationship - partners are often given access even where the
  public crawler policy says no.

## RORC legacy archive workflow (2007-2022)

1. **Discover race URLs for a season.** Fetch the season index page:
   `WebFetch("https://www.rorc.org/racing/race-results/<year>-results", "List every link (href + text) on this page, especially anything under /raceresults/<year>/")`
   This returns the slugs for every race/class in that season
   (e.g. `ircoverall11.html`, `zero10.html`).

2. **Extract one race's table.** For each results URL:
   ```
   WebFetch(
     url="https://www.rorc.org/raceresults/<year>/<slug>.html",
     prompt="Extract the full results table as CSV. Use exactly these "
            "columns in this order: Position,Points,SailNo,Boat,BoatType,"
            "Owner,SailedBy,FinishTime,Elapsed,Handicap,Corrected,Comments. "
            "Output ONLY the CSV (with a header row), one line per boat, "
            "for every row in the table - do not truncate or summarize. "
            "Also state the race name/date and total number of boats at "
            "the very top as a comment line starting with #."
   )
   ```
   Save the returned CSV to `exports/rorc_<year>_<slug>.csv`. **Sanity-check
   the row count against the "Total boats" comment line and spot-check a
   couple of rows against the visible page** before loading — WebFetch runs
   the extraction through a small model and can occasionally mis-split a
   row (see the two DNF rows in `exports/rorc_2021_castlerock_ircoverall.csv`
   for an example that needed a manual fix).

3. **Load it:**
   ```
   python3 scripts/load_rorc_csv.py db/marketshare.db exports/rorc_<year>_<slug>.csv \
     --regatta "<regatta series name>" --year <year> --race-name "<race name>" \
     --class "<class label from the page>" --source-url "<the page URL>"
   ```
   This is idempotent (`INSERT OR REPLACE` on `(race_id, boat_id)`) so
   re-running a page you've already loaded just refreshes it.

4. **Respect `Crawl-delay: 10`** — space consecutive `WebFetch` calls to the
   same domain by at least 10 seconds when pulling many pages in one run.

A full backfill of 2007-2022 across all RORC Inshore/Offshore classes is
several hundred individual race pages. `exports/rorc_2021_castlerock_ircoverall.csv`
is the one proof-of-concept race loaded so far (32 boats, real data) —
extending this to the full archive is mechanical repetition of steps 1-3
and is the natural next piece of work, best done a season at a time.

## Cowes Week: five seasons are behind a different page (found 2026-09-07)

`scrape_cowes_week.py` reads the DAILY results page (`page=results<year>`,
`dayrequest`/`classrequest`). Five seasons publish nothing there, which for a
while looked like the site simply not holding them:

    2007  2008  2009  2015  2018   - daily results return zero rows
    2020                           - genuinely nothing; the cancelled season

They are not missing. Those seasons publish OVERALL SERIES STANDINGS instead,
on the page the daily view links to as "View Overall Results":

    main_c.php?section=racing&page=points<year>&resultrequest=<seriesId>

`page=points<year>` with no `resultrequest` returns the series picker - a
`<select name="resultrequest">` whose options are the class list with numeric
ids. Class-level series available per season:

    2007: 49    2008: 43    2009: 44    2015: 51    2018: 55

That is roughly 240 class-seasons of boat-level data for five seasons that
currently hold none. Loaded by `scrape_cowes_points.py`, one season at a time,
via `scripts/cowes_points_backfill.sh`.

**Correction to the first version of this section:** it said these tables have
no sail numbers and so would have to be resolved by boat name, which is the
matching that produced every collision in `data/boat_merges.csv`. That was
wrong, and it mattered - it would have made this the riskiest load in the
project instead of one of the safest. Each standings row links to
`page=boatdetails<year>&boatref=<n>`, and that page carries the **sail number**,
design type, handicap/TCC, entered-by and skipper. Identity here is not
inferred at all; it is better than the daily scrape's, which has no TCC or
skipper. Only boats whose detail page is missing entirely (2018 refs 1333,
1388, 1418, 1552 return the site chrome and nothing else) fall back to a name,
and those are listed by name at the end of every run.

Things to get right when loading it:

  - **Skip "Black Group Overall" and "White Group Overall".** They are
    re-cuts of the same boats across the class series, exactly like the
    Double Handed and Line Honours views on Round the Island. Loading them
    alongside the class series enters every boat twice and inflates share.

  - **A cell says whether the boat sailed, and the codes are not obvious.**
    `DNC` (did not compete), `NER` and `NOD` are not entries; a blank code,
    `DNF`, `RET` and `DSQ` are. `NER` means "not entered for that race", which
    is settled rather than guessed: the boat detail page's "Days entered" field
    is an eight-character week (`SSMTWTFS`), and the NER days are exactly the
    days outside it - four boats checked, four exact matches. `NOD` is the one
    judgement: same penalty as DNC but on days the boat WAS entered for, no key
    published anywhere on the site, so it is treated as not-sailed and counted
    in every run's summary so the size of the doubt stays visible.

  - **Race naming maps the weekday to a day number**, giving
    `"<class> - Day <n>"` exactly as the daily scraper writes for 2010-2026, so
    a later daily load of these seasons merges instead of duplicating. The
    header row cannot be used directly: 2008 lists two Saturdays (first and
    last day of the week) and 2007's Class 0 IRC raced Fri, Wed, Thu in that
    order. 2015 numbers its columns R1-R7 with no weekday, so those keep the
    source's numbering as `"<class> - R<n>"`.

## Round the Island: browser-read, and now reproducible (2026-09-08)

`racing.islandsc.org.uk` disallows this project's crawler, so the 551 Round the
Island entries for 2025 and 2026 were read out of the user's own signed-in
browser and loaded with `load_rti_islandsc.py`. **The CSV was never saved.**
Every other source here can be rebuilt from a scraper, a tracked spreadsheet or
a tracked decisions file; this one could not, so a rebuild from scratch would
have silently lost the biggest fleet in the Solent.

`export_rti_source.py` reconstructs that file from the database into
`data/rti_islandsc.csv`, and the result is a real input rather than a report:

```
python3 scripts/export_rti_source.py db/marketshare.db
python3 scripts/load_rti_islandsc.py db/marketshare.db data/rti_islandsc.csv
```

Two things to know about it.

  - **A reload needs the correction ledgers afterwards.** Loading the file into
    a copy of the database gave 552 entries against the original's 551. The
    extra one is VENOMOUS, which Round the Island published under GBR7017R -
    Tortuga Marine's Botin 56 BLACK PEARL - and the CSV keeps that because it
    is what the source said. Re-running `merge_boats.py` removes it and
    re-identifies GBR7017R, after which the copy matches the original exactly.
    `refresh_all.py` already runs in that order, so a reload through the
    pipeline is faithful; a reload on its own is not.

  - **It is the loadable subset, not what the browser saw.** Only the IRC 0-3
    rows were ever stored - the Double Handed, Clipper Yachts, Line Honours and
    one-design views are re-cuts of those same boats and were discarded on
    purpose, so they cannot come back out of the database. The four IRC
    divisions partition the fleet exactly (2025: 28+83+71+84 = 266; 2026:
    34+73+82+96 = 285, both equal to the site's own overall list), which is why
    dropping the rest is right for entries and why this file is complete for
    every purpose the database serves.

The export is deliberately NOT part of `refresh_all.py`: it writes a source file
*from* the database, so scheduling it would let a damaged database overwrite the
only copy of its own source.
