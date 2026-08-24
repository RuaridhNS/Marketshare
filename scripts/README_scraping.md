# Scraping workflow

## Important environment constraint

This project is built and run inside a Claude cloud sandbox. That sandbox's
shell (`bash`/`python requests`/`curl`/etc.) has **no general internet
access** — only package registries are reachable directly. The only way
Claude can reach an arbitrary external website from here is the `WebFetch`
tool. This means:

- A conventional standalone `requests`/`BeautifulSoup` scraper **cannot run
  unattended in this sandbox** — there's nothing to schedule it on that has
  network access.
- Any "automated scraping" in this environment has to go through Claude
  itself calling `WebFetch` (or, on the user's own machine via the device
  bridge, a normal Python scraper *can* run, since that machine has real
  internet access).
- Practically: a scheduled Claude task (see the project root's task list)
  re-enters a session, calls `WebFetch` on each target results page with a
  structured-extraction prompt, saves the output as a staging CSV, then runs
  `load_rorc_csv.py` to upsert it into the database. Slower and more
  "expensive" (LLM calls) per page than a real scraper, but it's what's
  actually available from this sandbox today.

## Source scrapability audit (checked 2026-08-24)

| Source | robots.txt | Verdict |
|---|---|---|
| `rorc.org` (legacy static results, years **2007-2022**, e.g. `/raceresults/2021/ircoverall11.html`) | `Crawl-delay: 10`, no bot-specific restrictions | **Scrapable.** Plain HTML tables. |
| `sailracehq.com` (current RORC results, **2023-present** — RORC migrated their live-results platform) | `Content-Signal: ai-train=no`; explicitly disallows `ClaudeBot` and other AI crawlers | **Not scraped.** Site has opted out of AI crawler access. |
| `myjog.jog.org.uk` (JOG results, all years) | explicitly disallows `ClaudeBot` and other AI crawlers, cites EU copyright directive | **Not scraped.** Same reason. Continue manual export from this site, or ask JOG for an official data/API arrangement. |
| Cowes Week (`cowesweek.co.uk`) | no robots.txt found (404) | Not yet built. No explicit opt-out, but needs its own page-structure reconnaissance before building a loader. |
| RSYC, Warsash SC, ORC | not yet checked | Not yet built. Warsash results are partly PDF-based (see notes in the original IRC Solent Report — links to PDF summaries on warsashsc.org.uk). |

**Bottom line:** real automation is only available for the RORC legacy
archive (2007-2022) today. Current-season results on both of the platforms
this data currently comes from (SailRaceHQ for RORC, MyJOG for JOG) have
opted out of AI-crawler access at the platform level, so ongoing/current
results still need to come in some other way — manual entry (fastest to
keep going with what already works), or an official data-sharing
arrangement with RORC/JOG (worth asking, given the existing rep
relationship — sponsors/partners are often given API or CSV export access
even when the public crawler policy says no).

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
