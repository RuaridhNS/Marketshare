#!/usr/bin/env python3
"""
Load the JOG 2025 Offshore Series standings, transcribed from screenshots the
user supplied.

myjog.jog.org.uk disallows ClaudeBot in robots.txt, so this data cannot be
scraped - it was provided directly. That is why it lives in a hand-written
script rather than a scraper.

These are SERIES STANDINGS (one row per boat per class with a season total),
not per-race results, so each class loads as a single standings "race" the way
the Warsash PDF years do.

Double Handed is treated as a LAYER, not a fourth class: every boat in the DH
table also appears in Class 1, 2 or 3, so loading DH separately would give
those boats a second entry and double-count them in every fleet total. Instead
the DH boats are tagged on their existing class entry.

The number embedded in the Type string ("SUN FAST 3300 1.95", "JPK 10.80 2.15
fin6") is NOT an IRC TCC - a Sun Fast 3300 rates about 1.02 - so it is left in
the type text and deliberately not loaded into the tcc column.

Usage:
  python3 load_jog_2025_offshore.py <db.sqlite> [--dry-run]
"""
import sys
import csv
import argparse
import sqlite3
import subprocess

SCRIPT_DIR = __import__("pathlib").Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

# (rank, sail_no, boat, type, owner, total)
CLASS1 = [
    (1, "GBR4436L", "MZUNGU!", "JPK 10.80 2.15 fin6", "Tony White", 4069),
    (2, "GBR5964R", "SANITY", "SUN FAST 3300 1.95", "Carol Lo", 3186),
    (3, "GBR8936R", "RORC Griffin", "Sunfast 3600", "RORC", 3019),
    (4, "GBR833X", "Hooligan VIII", "SUN FAST 3300 1.95 WB", "Edward Broadway", 2587),
    (5, "GBR8657L", "Bellino", "SUN FAST 3600 2.20 Fin6", "Rob Craigie", 2322),
    (6, "GBR6779R", "Kestrel", "SUN FAST 3300", "Simon Bamford", 1901),
    (7, "GBR6586L", "SCREAM 2", "J 120 2.13", "Stuart Lawrence", 1515),
    (8, "GBR6988R", "IN CODE", "JPK 10.30 2.00", "Peter Bacon", 1202),
    (9, "IRL39000", "ZERO II", "MILLS 39 Custom", "Thomas Wilson", 1164),
    (10, "GBR809", "Lutine", "X 55 3.20", "Lloyd's of London Yacht Club", 1151),
    (11, "GBR2993L", "MINNIE THE MINX", "FIRST 40 2.48", "Richard Catchpole", 1123),
    (12, "GBR8940R", "ESPRESSO MARTINI TOO", "FARR 40 2.60", "Cameron Davis", 641),
    (13, "FRA16559", "LATITUDE", "X119", "BRUNO RATCLIFFE", 624),
    (14, "GBR1419L", "Sidney II", "GRAND SOLEIL 50 2.35", "Bob Mechem & Tanya Sullivan", 624),
    (15, "GBR5176L", "GameOn", "SUN FAST 3300 1.95", "Ian Hoddle", 578),
    (16, "GBR659R", "Troubadour", "SWAN 46 2.50", "Andy Roy", 549),
    (17, "GBR1693R", "Amity", "POGO 12.50 3.00", "Philip Avery", 499),
    (18, "GBR1694R", "Exuberant", "X 412 Mk3 2.10", "Robert Hillman", 499),
    (19, "GBR5731R", "Stickleback", "J 45 2.30", "Nick & Jacquetta Edmonds", 443),
    (20, "GBR8920R", "JET", "J 120 2.13", "James and John Owen", 297),
    (21, "GBR3481L", "DELTA X", "X 412 Mk2 2.10", "Tom Chicken", 239),
    (22, "GBR2741R", "Batfish V", "COMET 41 S 2.40", "Bill Blain", 50),
    (23, "GBR979R", "Malice", "HOD 35 2.20", "Mike Moxley", 50),
    (23, "GBR5591L", "ONE WAY", "SUN FAST 3600 2.20 Fin6", "Unity Sailing", 50),
    (23, "GBR6593T", "PETRUCHIO", "FIRST 40.7 2.40", "LCSC", 50),
    (23, "GBR5985L", "THINKING SPACE", "J 112 E 2.23 Fin6", "Harry Tilling", 50),
    (23, "GBR7727T", "Musketeer", "FIRST 40.7 2.40", "Nigel Wilson & Phil Holroyd Smith", 50),
]

CLASS2 = [
    (1, "GBR1010X", "JETPACK", "JPK 10.10 1.98 T", "Mark & Ella Brown", 4944),
    (2, "GBR9265R", "BORACIC", "GRAND SOLEIL 37 B&C 2.40", "Calum McKie", 3245),
    (3, "GBR4867L", "Purple Mist", "SUN FAST 3200 R2 1.90", "Kate Cope", 2523),
    (4, "GBR8643T", "Arcsine", "ARCONA 370 2.00", "kathy claydon", 2503),
    (5, "GBR2904L", "HOT PURSUIT", "SUN FAST 3200 R2 1.90", "Adam Brooks", 2342),
    (6, "GBR960R", "HOT RATS", "FIRST 35 2.20 (09)", "Robbie & Lis Robinson", 1980),
    (7, "GBR8134T", "MOJO", "J 105 1.98", "Richard Breese", 1691),
    (8, "GBR8367T", "Red Hawk", "FIRST 36.7 2.20", "James Armstrong", 1282),
    (9, "GBR529R", "Just So", "J 109 2.10", "David and Will McGough", 1151),
    (10, "GBR922R", "Juno", "X 34 1.90", "Simon Bottoms", 1145),
    (11, "GBR9203R", "Wee Bear", "PROJECTION 920 MOD keel", "Nick Lee", 1101),
    (12, "GBR6664R", "Step On", "SUN FAST 3200 R2 1.90", "Mark Emons", 863),
    (13, "GBR8709", "Lady Jane", "J109", "Georges BOUVARD", 800),
    (14, "IRL8088", "FRANK 4", "J 109 2.10", "Olly & Sam Love", 703),
    (15, "FRA43653", "Nirvana", "SUN FAST 3200R1 1.90", "Keith THOMPSON", 697),
    (16, "GBR7699R", "SNAPSHOT", "J 99 2.10 Fin6", "Charles Balmain", 665),
    (17, "GBR8932R", "FULL CIRCLE", "SUN FAST 3200 R2 1.90", "Stuart Wilkie", 658),
    (18, "GBR1575L", "Pure Attitude", "X 37 1.98", "Martin Gray", 481),
    (19, "GBR740L", "Gracor", "DUFOUR 40 2.10", "Tim Buckley", 419),
    (20, "GBR8725R", "HAIR OF THE DOG", "SUN FAST 3200 1.90", "Chris Baldwin", 364),
    (21, "GBR1535R", "White Cloud ix", "HOD 35 2.20", "John, Alison & Nick Donnelly", 335),
    (22, "GBR7005R", "TROJAN", "J 109 2.10", "Royal Engineer Yacht Club", 256),
    (23, "GBR3373L", "Azygos", "DUFOUR 40 2.10", "Dave Stott", 239),
    (24, "GBR9034R", "Minx4", "X 34 1.90", "Jonathan Gardiner", 226),
    (25, "GBR9487R", "Jumunu", "J 109 2.10", "Lesley Brooman", 188),
    (26, "GBR9779T", "JAGO", "J 109 2.10", "Philip Morgan", 151),
    (27, "GBR3737L", "Unruly", "X 37 1.98", "charles bull", 147),
    (28, "GBR9066R", "BLACKJACK", "J 99 2.00", "Vernon Bradley", 50),
    (28, "GBR4799R", "JIRO", "J 99 2.00", "Mark Kendall", 50),
]

CLASS3 = [
    (1, "GBR9911Y", "SailFish", "GIBSEA 90", "Oli Hawkins", 5505),
    (2, "GBR815", "Longue Pierre", "DEHLER 38CR 1.90", "David Cooper, Paul England", 2859),
    (3, "GBR8338", "With Alacrity", "SIGMA 38", "Chris Choules", 2689),
    (4, "GBR754R", "Xtract", "X 302 MK2 1.70", "Dudley Stock", 2518),
    (5, "GBR9587R", "Spectrum", "IMPALA 28 I/B MOD (keel)", "Joe Simmons", 1901),
    (6, "GBR123", "Xara", "SWAN 38 2.00", "Jonathan and Annie Rolls", 1600),
    (7, "GBR6388T", "MARTA", "SIGMA 38", "Brian Skeet", 1504),
    (8, "GBR5905R", "Javelin", "J 105", "Thomas Newsom", 1265),
    (9, "GBR6858T", "First Light", "ELAN 333 1.90", "Chris Flewitt", 1111),
    (10, "GBR4353", "Blues", "SIGMA 33 OOD", "Brendan Treacy", 1015),
    (11, "GBR9317R", "Sapphire", "FIRST 31.7 1.90", "Nick Dickinson", 811),
    (12, "GBR7732T", "Mardy Gras", "X 332 1.81", "Fred Mundle", 775),
    (13, "GBR1540C", "BLAZER", "LASER 28 MOD keel 1.64", "Colin Woodruff", 775),
    (14, "GBR9909", "MARKOVA", "Swan 36 (Mk 2)", "Gavin Marriott", 624),
    (15, "GBR8285", "KINDRED SPIRIT", "SIGMA 38", "Kindred Spirit Sailing LLP", 549),
    (16, "GBR3552T", "Option", "GRAND SOLEIL 343 1.80", "Alan Jurd", 472),
    (17, "GBR430", "Overlord", "100 SQ METRE", "Offshore Cruising Club", 402),
    (18, "GBR9690R", "Adelie", "X 332 1.81", "Rob Salter", 370),
    (19, "GBR8694T", "Double Trouble", "SJ 320/SEAQUEST 32 1.97", "David Thompson", 311),
    (20, "GBR6455R", "Gunsmoke", "SJ 320/SEAQUEST 32 1.97", "Anthony Tahourdin", 286),
    (21, "GBR4350L", "Felix", "X 332 1.81", "Christoph Friedrich", 50),
    (21, "CO121", "Rose of Purbecks", "CONTESSA 26", "Steve Richardson", 50),
    (23, "GBR7696T", "MEMORY MAKER", "FIRST 31.7 1.90", "Don Forster", 0),
]

# Double Handed standings - sail numbers only. Every one of these also appears
# in a class above, so they are applied as a tag rather than loaded as entries.
DOUBLE_HANDED = [
    "GBR4867L", "GBR5964R", "GBR6988R", "GBR8643T", "GBR4436L", "GBR8134T",
    "GBR5905R", "GBR6388T", "GBR6664R", "GBR9911Y", "GBR9203R", "GBR8657L",
    "GBR5176L", "GBR8338", "GBR6858T", "GBR1419L", "FRA43653", "GBR7727T",
    "GBR979R", "GBR5985L", "GBR4350L", "GBR4799R", "GBR7696T",
]

REGATTA = "JOG Offshore Series"
YEAR = 2025
SOURCE = "JOG 2025 Offshore Series standings (supplied by user, myjog.jog.org.uk)"

COLS = ["Position", "Points", "SailNo", "Boat", "BoatType", "Owner",
        "SailedBy", "FinishTime", "Elapsed", "Handicap", "Corrected", "Comments"]


def write_csv(rows, path, comment):
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write(f"# {comment}\n")
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for rank, sail, boat, btype, owner, total in rows:
            w.writerow({"Position": rank, "Points": total, "SailNo": sail,
                        "Boat": boat, "BoatType": btype, "Owner": owner,
                        "SailedBy": "", "FinishTime": "", "Elapsed": "",
                        "Handicap": "", "Corrected": "", "Comments": ""})


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    exports = REPO_ROOT / "exports"
    exports.mkdir(exist_ok=True)

    for label, rows in (("IRC 1", CLASS1), ("IRC 2", CLASS2), ("IRC 3", CLASS3)):
        safe = label.replace(" ", "")
        path = exports / f"jog_2025_offshore_{safe}.csv"
        race_name = f"JOG 2025 Offshore Series - {label} standings"
        write_csv(rows, path, f"{race_name} - {len(rows)} boats - {SOURCE}")
        print(f"{label}: {len(rows)} boats -> {path.name}")
        if args.dry_run:
            continue
        cmd = [sys.executable, str(SCRIPT_DIR / "load_rorc_csv.py"), args.db, str(path),
               "--regatta", REGATTA, "--year", str(YEAR), "--race-name", race_name,
               "--class", label, "--category", "JOG",
               "--source-url", SOURCE, "--source", "manual:jog-2025-offshore"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        print("  " + (r.stdout.strip() or r.stderr.strip()[:200]))

    if args.dry_run:
        print("\n(dry run - nothing written)")
        return

    # Double Handed as a layer on the existing class entries
    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    q = ",".join("?" * len(DOUBLE_HANDED))
    n = cur.execute(f"""
        UPDATE race_entries SET tag = 'Double Handed'
        WHERE boat_id IN (SELECT id FROM boats WHERE sail_no IN ({q}))
          AND race_id IN (
            SELECT ra.id FROM races ra
            JOIN events e ON e.id = ra.event_id
            JOIN regattas g ON g.id = e.regatta_id
            WHERE g.name = ? AND e.season_year = ?)""",
        (*DOUBLE_HANDED, REGATTA, YEAR)).rowcount
    conn.commit()
    missing = [s for s in DOUBLE_HANDED if not cur.execute(
        "SELECT 1 FROM boats WHERE sail_no = ?", (s,)).fetchone()]
    print(f"\nTagged {n} entr(y/ies) as Double Handed across the three classes.")
    if missing:
        print(f"  DH sail numbers with no boat record: {missing}")
    conn.close()


if __name__ == "__main__":
    main()
