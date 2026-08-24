-- Marketshare / Race Entry Monitoring Tool
-- SQLite schema. Boat is the central entity: everything else (owners,
-- sailmakers, results, CRM status) hangs off boats over time.
--
-- Design notes:
--  * A boat is identified primarily by its most recent sail number, but
--    sail numbers and names can change (re-registration, resale, rename).
--    boats.id is the durable identity; boat_name/sail_no on the boats
--    table are just "current known" convenience fields. Every historical
--    name/sail-no actually used is preserved on race_entries.*_used so
--    nothing is lost even if matching later boats needs manual correction.
--  * owner is a person/entity, resolved (fuzzy-matched, then confirmed)
--    from the free-text "Owner" field seen in entries.
--  * sailmaker tracking is the market-share core: both a point-in-time
--    value per entry (what we scraped/logged for that race) and a
--    boat_sailmaker_history table for the CRM's "current belief" per boat,
--    since that's often known even between races.
--  * event_class_counts holds the aggregate, non-boat-level historical data
--    (e.g. IRC Solent Report) for years/regattas where only class totals
--    are available, not a boat-level breakdown.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sailmakers (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,          -- 'North Sails', 'Doyle', 'Ullman', 'Quantum', 'Sanders', 'Partial', 'Unknown', 'Other', ...
    is_us       INTEGER NOT NULL DEFAULT 0,    -- 1 = this is our brand (North Sails)
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS owners (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,                 -- normalized display name
    UNIQUE(name)
);

CREATE TABLE IF NOT EXISTS boats (
    id              INTEGER PRIMARY KEY,
    sail_no         TEXT UNIQUE,                -- most recently observed sail number (natural key for matching)
    boat_name       TEXT,                       -- most recently observed name
    boat_type       TEXT,                       -- most recently observed type/rig (e.g. 'J 111 2.20 EU')
    tcc             REAL,                       -- most recently observed IRC TCC rating
    current_owner_id INTEGER REFERENCES owners(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- CRM fields, one row per boat, editable by the sales team.
CREATE TABLE IF NOT EXISTS boat_crm (
    boat_id         INTEGER PRIMARY KEY REFERENCES boats(id) ON DELETE CASCADE,
    lead_rep        TEXT,
    contacted_by    TEXT,
    in_cs           INTEGER,            -- 1/0/NULL = in customer system
    tag             TEXT,               -- free-text tag from the original 'Boats' sheet
    notes           TEXT,
    last_updated    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Which sailmaker a boat uses, over time. Multiple rows per boat as it changes.
CREATE TABLE IF NOT EXISTS boat_sailmaker_history (
    id              INTEGER PRIMARY KEY,
    boat_id         INTEGER NOT NULL REFERENCES boats(id) ON DELETE CASCADE,
    sailmaker_id    INTEGER REFERENCES sailmakers(id),
    effective_from  TEXT,               -- date (ISO) or NULL if unknown
    effective_to    TEXT,               -- NULL = current
    source          TEXT,               -- 'manual:jog_fleet_combined', 'scrape:jog', etc.
    confidence      TEXT DEFAULT 'manual'  -- 'manual' | 'inferred'
);

-- A regatta / race series "brand" (not year-specific).
CREATE TABLE IF NOT EXISTS regattas (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,       -- 'JOG Lonely Tower', 'RORC Inshore', 'Cowes Week', 'RSYC May Regatta', ...
    category    TEXT,                       -- 'JOG' | 'RORC' | 'Club' | 'Championship'
    region      TEXT DEFAULT 'Solent'
);

-- A specific running of a regatta in a given season.
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    regatta_id  INTEGER NOT NULL REFERENCES regattas(id),
    season_year INTEGER NOT NULL,
    start_date  TEXT,
    end_date    TEXT,
    source_url  TEXT,
    notes       TEXT,
    UNIQUE(regatta_id, season_year)
);

-- An individual race within an event (JOG series events have many; a
-- single-race regatta just has one race row).
CREATE TABLE IF NOT EXISTS races (
    id          INTEGER PRIMARY KEY,
    event_id    INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    race_name   TEXT,
    race_number INTEGER,
    race_date   TEXT,
    status      TEXT,               -- 'confirmed' | 'provisional' | ...
    source_url  TEXT
);

-- Boat-level entry + result for one race. Result columns are NULL until
-- the race has been sailed and results scraped/entered - this is what
-- lets us "monitor entries over the course of an event" pre-race.
CREATE TABLE IF NOT EXISTS race_entries (
    id                  INTEGER PRIMARY KEY,
    race_id             INTEGER NOT NULL REFERENCES races(id) ON DELETE CASCADE,
    boat_id             INTEGER NOT NULL REFERENCES boats(id),
    class               TEXT,               -- IRC class/division at time of entry
    sail_no_used        TEXT,
    boat_name_used      TEXT,
    boat_type_used      TEXT,
    tcc                 REAL,
    owner_id            INTEGER REFERENCES owners(id),
    owner_name_used     TEXT,
    skipper_name_used   TEXT,
    sailmaker_id        INTEGER REFERENCES sailmakers(id),
    status              TEXT,               -- 'entered' | 'finished' | 'dnf' | 'dns' | 'ocs' | 'dsq' | 'retired'
    finish_time         TEXT,
    elapsed_time        TEXT,
    corrected_time       TEXT,
    position            INTEGER,
    points               REAL,
    comments             TEXT,
    lead_rep             TEXT,               -- CRM snapshot at time of entry (may differ from current boat_crm)
    contacted_by          TEXT,
    in_cs                 INTEGER,
    tag                   TEXT,
    source                TEXT NOT NULL,     -- 'manual:jog_fleet_combined' | 'manual:irc_solent_report' | 'scrape:jog' | 'scrape:rorc' | ...
    scraped_at             TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(race_id, boat_id)
);

-- Aggregate, non-boat-level historical counts (e.g. IRC Solent Report:
-- class totals per regatta per year, going back to 2017/18, with no
-- boat-level breakdown available for older years).
CREATE TABLE IF NOT EXISTS event_class_counts (
    id          INTEGER PRIMARY KEY,
    event_id    INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    class_label TEXT NOT NULL,
    entry_count INTEGER,
    source      TEXT NOT NULL,
    notes       TEXT,
    UNIQUE(event_id, class_label, source)
);

-- Audit trail for scraper runs.
CREATE TABLE IF NOT EXISTS scrape_runs (
    id              INTEGER PRIMARY KEY,
    source_name     TEXT NOT NULL,      -- 'jog' | 'rorc' | ...
    started_at      TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at     TEXT,
    status          TEXT,               -- 'success' | 'partial' | 'failed'
    records_upserted INTEGER,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_race_entries_boat ON race_entries(boat_id);
CREATE INDEX IF NOT EXISTS idx_race_entries_race ON race_entries(race_id);
CREATE INDEX IF NOT EXISTS idx_race_entries_sailmaker ON race_entries(sailmaker_id);
CREATE INDEX IF NOT EXISTS idx_races_event ON races(event_id);
CREATE INDEX IF NOT EXISTS idx_events_regatta_year ON events(regatta_id, season_year);
CREATE INDEX IF NOT EXISTS idx_boat_sailmaker_history_boat ON boat_sailmaker_history(boat_id);
