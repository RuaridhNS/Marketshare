# Extraction prompt for Claude in Chrome

Paste this into Claude in Chrome with a results page open. It produces a CSV
that `scripts/load_pasted_results.py` reads directly — self-describing, so one
paste can cover many races and classes.

Use it for **JOG** and any **RORC** page not covered by the season-points
workbooks. Nothing here bypasses a site's access controls: it transcribes a page
already open in your own browser, under your own login.

Load the result with:

```bash
python3 scripts/load_pasted_results.py db/marketshare.db pasted.csv --category JOG
```

---

## The prompt

> You are transcribing sailing race results from the page currently open in this
> browser. Accuracy matters far more than completeness: this feeds a database,
> and an invented value is worse than a missing one.
>
> **Output nothing but a CSV** — no commentary before or after it, no markdown
> fence. First line is the header, exactly:
>
> `Regatta,SeasonYear,RaceName,ClassLabel,RaceDate,Position,Points,SailNo,BoatName,BoatType,Owner,SailedBy,TCC,Elapsed,Corrected,Status,DoubleHanded,Sailmaker,SailmakerPartial,SourceUrl`
>
> **One row per boat per race.** If a page shows six races for one class, a boat
> that sailed all six produces six rows. If the page is a series standings table
> with one column per race, produce one row per boat per race column that shows
> the boat actually competed — a cell reading DNC, or blank, means it did not,
> so no row for that race.
>
> **Column rules**
>
> - `Regatta` — the event as the page names it, e.g. `JOG Offshore Series`. Same
>   spelling on every row from this page.
> - `SeasonYear` — four digits.
> - `RaceName` — the individual race. If the page gives none, use `Race 1`,
>   `Race 2` in the order shown. Never merge two races into one row.
> - `ClassLabel` — the division exactly as printed: `IRC 1`, `IRC Two Handed`,
>   `Class 3`. Do not translate or tidy it.
> - `RaceDate` — `YYYY-MM-DD` only if the page states it. Otherwise blank.
> - `SailNo` — as printed, including the country prefix (`GBR1234R`). This is
>   the key the database matches on, so never abbreviate or reformat it. If a
>   row has no sail number, still emit the row with `SailNo` blank and the
>   `BoatName` filled in.
> - `TCC` — the IRC rating, typically between 0.8 and 2.0. Blank if absent.
> - `Status` — only if the page says so: `DNF`, `DNS`, `DNC`, `RET`, `DSQ`, `OCS`.
> - `DoubleHanded` — `Y` if this row comes from a two-handed division or the
>   page marks the boat as double-handed, otherwise `N`. Double-handed boats
>   usually appear in their IRC division **as well**; emit both rows. They are a
>   layer over the fleet, not a separate fleet.
> - `Sailmaker` — only if the page actually names one. Never infer it from the
>   boat, the owner, or anything you know outside this page.
> - `SailmakerPartial` — `Y` if the sailmaker is marked with an asterisk or the
>   page says the boat carries only some of that maker's sails. Otherwise `N`.
> - `SourceUrl` — the page URL, same on every row.
>
> **Rules that override everything else**
>
> 1. Transcribe only what is visibly on the page. Do not fill a gap from memory,
>    from another page, or from what looks likely. **A blank cell is correct
>    when the page does not show the value.**
> 2. Do not reorder, rank, deduplicate, correct spellings, or expand
>    abbreviations. Boat names and owner names go through exactly as printed,
>    including odd capitalisation.
> 3. Include **every** class on the page, one-design fleets included. The loader
>    filters to IRC itself; if you drop them, I cannot see what was there.
> 4. If the page paginates, or a class is behind a tab or a dropdown, transcribe
>    what is currently loaded and then tell me, in one line **after** the CSV,
>    which classes or pages you could not reach.
> 5. If a value is ambiguous — two boats sharing a sail number, a column you
>    cannot identify — emit the row and note it in that same line after the CSV.
>
> **Finally**, after the CSV, output one line in this form so I can check
> nothing was lost:
>
> `# rows=<N> classes=<list> races=<list> notreached=<list or none>`
