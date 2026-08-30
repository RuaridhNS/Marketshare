#!/usr/bin/env python3
"""
Undo an invented regatta, and collapse the Easter Challenge into one.

Three separate faults, all in the same corner of the data:

1. THE PHANTOM. build_db.py carried a title override renaming the "Easter
   challenge" block of IRC Solent Report.xlsx to "RORC Inshore Series". The
   sheet's title was correct and the override was wrong: there is no RORC
   Inshore Series. Nine years of class entry counts ended up filed under a
   regatta that does not exist.

2. THE SCATTER. The scraped Easter Challenge results never consolidated. The
   same annual regatta is spread over six regatta rows because the CLASS was
   baked into the regatta name - "RORC Easter Challenge - Fast 40+", "... IRC
   Four A", "... J/80 Class" - and one catch-all, "RORC Easter Challenge -",
   which dropped the class entirely. The class belongs in the class layer, not
   the regatta name, or the dashboard shows six Easter Challenges.

3. THE STOWAWAY. A 2021 Castle Rock Race sits under the phantom regatta. It is
   not an Easter Challenge race, and the spreadsheet records the 2021 Easter
   Challenge as nil (it was not sailed). Folding it in would invent 32 entries
   in a year that had none, so it is moved out to stand on its own.

What this does NOT do: those scraped "races" are not races. Each row's name is
a Sailwave standings header ("Entries: 15     Races Sailed: 8"), so each is one
class's final standings for the year. The entry counts in them are real; the
race count is not. This script preserves the class in the race row rather than
inventing per-race results it does not have.

Idempotent: safe to run twice. Run with --dry-run first.

Usage:
  python3 fix_easter_challenge.py <db.sqlite> [--dry-run]
"""
import re
import argparse
import sqlite3

PHANTOM = "RORC Inshore Series"
TARGET = "RORC Easter Challenge"
STOWAWAY = "Castle Rock Race"
STOWAWAY_HOME = "RORC Castle Rock Race"

# Regatta name -> the class those rows actually belong to. The catch-all and
# the bare name carry no class, so their rows keep an unrecorded division
# rather than being handed a made-up one.
CLASS_FROM_NAME = re.compile(r"^RORC Easter Challenge\s*[-–]?\s*(.*)$", re.I)


def class_of(regatta_name):
    m = CLASS_FROM_NAME.match(regatta_name)
    if not m:
        return None
    cls = m.group(1).strip()
    cls = re.sub(r"\s*\bclass\b\s*$", "", cls, flags=re.I).strip()
    return cls or None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    def rid(name):
        r = cur.execute("SELECT id FROM regattas WHERE name = ?", (name,)).fetchone()
        return r[0] if r else None

    # ---- 1. the canonical regatta everything lands on -----------------------
    target = rid(TARGET)
    if target is None:
        cur.execute("INSERT INTO regattas (name, category, region) VALUES (?, ?, ?)",
                    (TARGET, "RORC", "Solent"))
        target = cur.lastrowid
        print(f"created regatta {TARGET!r} (id {target})")
    else:
        print(f"target regatta {TARGET!r} is id {target}")

    # ---- 2. move the stowaway out before anything else ----------------------
    stow = cur.execute(
        "SELECT ra.id, e.id, e.season_year FROM races ra JOIN events e ON e.id = ra.event_id "
        "JOIN regattas r ON r.id = e.regatta_id WHERE r.name = ? AND ra.race_name = ?",
        (PHANTOM, STOWAWAY)).fetchall()
    for race_id, old_event, year in stow:
        home = rid(STOWAWAY_HOME)
        if home is None:
            cur.execute("INSERT INTO regattas (name, category, region) VALUES (?, ?, ?)",
                        (STOWAWAY_HOME, "RORC", "Solent"))
            home = cur.lastrowid
        ev = cur.execute("SELECT id FROM events WHERE regatta_id = ? AND season_year = ?",
                         (home, year)).fetchone()
        if ev is None:
            cur.execute("INSERT INTO events (regatta_id, season_year) VALUES (?, ?)",
                        (home, year))
            ev = (cur.lastrowid,)
        cur.execute("UPDATE races SET event_id = ? WHERE id = ?", (ev[0], race_id))
        n = cur.execute("SELECT COUNT(*) FROM race_entries WHERE race_id = ?",
                        (race_id,)).fetchone()[0]
        print(f"moved {STOWAWAY!r} ({year}, {n} entries) out to {STOWAWAY_HOME!r}")

    # ---- 3. fold every source regatta into the target -----------------------
    sources = cur.execute(
        "SELECT id, name FROM regattas WHERE (name = ? OR name LIKE 'RORC Easter Challenge%') "
        "AND id != ? ORDER BY name", (PHANTOM, target)).fetchall()

    moved_races = moved_counts = moved_events = 0
    for src_id, src_name in sources:
        cls = None if src_name == PHANTOM else class_of(src_name)
        for ev_id, year in cur.execute(
                "SELECT id, season_year FROM events WHERE regatta_id = ?", (src_id,)).fetchall():
            dest = cur.execute("SELECT id FROM events WHERE regatta_id = ? AND season_year = ?",
                               (target, year)).fetchone()
            if dest is None:
                cur.execute("INSERT INTO events (regatta_id, season_year) VALUES (?, ?)",
                            (target, year))
                dest_id = cur.lastrowid
            else:
                dest_id = dest[0]

            # Carry the class down onto the race row AND onto the entries
            # before the regatta name that held it disappears. The entry-level
            # class is what the export's IRC filter reads, so leaving it null
            # is how eight J/80s were counting as IRC boats.
            if cls:
                for race_id, rn in cur.execute(
                        "SELECT id, race_name FROM races WHERE event_id = ?", (ev_id,)).fetchall():
                    if not rn or not rn.lower().startswith(cls.lower()):
                        cur.execute("UPDATE races SET race_name = ? WHERE id = ?",
                                    (f"{cls} - {rn}" if rn else cls, race_id))
                    cur.execute(
                        "UPDATE race_entries SET class = ? "
                        "WHERE race_id = ? AND (class IS NULL OR TRIM(class) = '')",
                        (cls, race_id))
            n = cur.execute("SELECT COUNT(*) FROM races WHERE event_id = ?", (ev_id,)).fetchone()[0]
            cur.execute("UPDATE races SET event_id = ? WHERE event_id = ?", (dest_id, ev_id))
            moved_races += n

            # Class counts key on (event_id, class_label, source); a clash means
            # the same figure already landed, so drop the duplicate.
            for cc_id, label, src in cur.execute(
                    "SELECT id, class_label, source FROM event_class_counts WHERE event_id = ?",
                    (ev_id,)).fetchall():
                clash = cur.execute(
                    "SELECT id FROM event_class_counts WHERE event_id = ? AND class_label = ? "
                    "AND source = ?", (dest_id, label, src)).fetchone()
                if clash:
                    cur.execute("DELETE FROM event_class_counts WHERE id = ?", (cc_id,))
                else:
                    cur.execute("UPDATE event_class_counts SET event_id = ? WHERE id = ?",
                                (dest_id, cc_id))
                    moved_counts += 1

            cur.execute("DELETE FROM events WHERE id = ?", (ev_id,))
            moved_events += 1
        cur.execute("DELETE FROM regattas WHERE id = ?", (src_id,))
        print(f"  folded [{src_id}] {src_name!r}" + (f"  (class: {cls})" if cls else ""))

    print(f"\n{len(sources)} regatta(s) folded into {TARGET!r}: "
          f"{moved_events} event-year(s), {moved_races} race row(s), "
          f"{moved_counts} class-count row(s).")

    yrs = cur.execute(
        "SELECT e.season_year, COUNT(DISTINCT ra.id), "
        "  (SELECT COUNT(DISTINCT re.boat_id) FROM races r2 JOIN race_entries re ON re.race_id = r2.id "
        "   WHERE r2.event_id = e.id), "
        "  (SELECT COUNT(*) FROM event_class_counts c WHERE c.event_id = e.id) "
        "FROM events e LEFT JOIN races ra ON ra.event_id = e.id "
        "WHERE e.regatta_id = ? GROUP BY e.id ORDER BY e.season_year", (target,)).fetchall()
    print(f"\n{TARGET} now reads:")
    print("  year  races  boats  class-counts")
    for y, nr, nb, nc in yrs:
        print(f"  {y}   {nr:>4}   {nb:>4}   {nc:>4}")

    if args.dry_run:
        conn.rollback()
        print("\n(dry run - nothing written)")
    else:
        conn.commit()
        print("\ncommitted")
    conn.close()


if __name__ == "__main__":
    main()
