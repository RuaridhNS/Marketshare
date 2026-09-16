# Marketshare workbook import

Loads the hand-kept North Sails Marketshare workbooks (one .xlsx per event,
kept in OneDrive under `Marketshare backups/.../Marketshare Folder`) into the
database, so the boat-centric record stops being Solent-only.

Run in order, from this directory. Stages 1–4 only write staging CSVs; nothing
touches the database until `05_load.py --apply`.

| | script | what it does |
|---|---|---|
| 1 | `01_extract.py` | Every boat row out of every sheet → `staging_entries.csv`. Hunts the header row, maps ~60 column-name variants onto one canonical set, skips the sailmaker picklist sheets and the empty templates. |
| 2 | `02_events.py` | Groups rows into events → `staging_events.csv`. Cleans the event name, parses the date range out of the filename, and splits a workbook that carries **year-named sheets** into one event per season. |
| 3 | `03_classify.py` | Marks each event UK / already-drafted, and tests how many of its boats match boats we already hold. |
| 4 | `04_segments.py` | Assigns the commercial segment → `data/event_segments.csv`, an editable ledger. |
| 5 | `05_load.py` | Writes to the database. `--apply` to commit; without it, a dry run that rolls back. |

## The two dispositions

The brief was: *"Any UK or already drafted event please match boats or discard."*

- **match-or-discard** — UK events and events already in the database. Every boat
  is matched against an existing record; a row that cannot be matched is
  **discarded**, never inserted. A hand-kept spreadsheet can therefore never
  fork the boat table.
- **new event** — everything else. Creates the regatta, event, an "Entry List"
  race and, where needed, new boats.

## Segments

`Grand Prix`, `Premier Race`, `Race`, `One-Design`, `Classic`, `Cruise`,
`Premier Cruise`. Rules live in `04_segments.py`, ordered — first match wins,
most specific first. `One-Design` is driven off **class names**, not off words
like "World Championship", so it cannot swallow the IRC and ORC championships,
which are handicap events.

`Cruise` and `Premier Cruise` are for rallies such as the ARC and the Oyster
World Rally. The corpus contains no rally workbooks yet, so `Cruise` is empty
and `Premier Cruise` holds the superyacht regattas.

## Idempotency

`05_load.py` tags everything it creates with
`races.source_url = 'marketshare-xlsx:<relative path>'` and deletes those rows
before re-importing, so re-running replaces rather than duplicates. It takes a
timestamped database backup first.

## Gotchas found the hard way

- Some sheets declare a phantom dimension of a million rows; the row scan is
  capped and stops after a long run of blanks.
- Superyacht workbooks head the boat column **`Team`**, with `Size` / `Design` /
  `Shipyard` instead of a sail number.
- A workbook may hold **two seasons as two year-named sheets** (Silverrudder,
  Les Voiles de Saint-Tropez). Collapsing those into one event silently deletes
  the earlier year — stage 2 splits them.
- A bare sail-number core is **not** enough to match a boat: one-design fleets
  reuse plain numbers, so "1234" can be both a J/70 and a Lightning. The match
  needs the boat name to agree as well.
- Sailmaker is recorded on roughly three rows in ten, and it gets recorded most
  often when the answer is North. Treat any North share off this data as a
  ceiling, not a measurement.
