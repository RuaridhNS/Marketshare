#!/usr/bin/env python3
"""Find official names that are really a sponsor or one-off name.

"The Mighty Incisor was a temporary name." A boat that raced 125 times as
SUNRISE and 7 times as SUNRISE BY THE ADVENTURER is called SUNRISE; the longer
string is a sponsor, a team name or a season's branding, and the latest-season
rule adopts it anyway because it happened to be used last.

The shape is specific, so it can be found rather than guessed at: the official
name CONTAINS a shorter name, as whole words, and the boat used the shorter one
far more often.

Two guards keep it honest:

  * The official name must have MORE WORDS, not merely more characters.
    Otherwise 'ARCTURUS OF LYMINGTO' - a truncated entry - beats
    'ARCTURUS OF LYMINGTON' on frequency and the boat is renamed to a typo.

  * The shorter name must be used at least --ratio times as often. Below that
    the two are competing names rather than a name and a label, and a human
    should decide: JENNY vs JENNY XXX at 52 to 20 is not obvious, and neither
    is DESPERADO vs DESPERADO OF COWES at 46 to 38.

Writes the confident ones to data/boat_name_overrides.csv, which
normalise_boat_names.py reads and which you can edit or empty at any time.

    python scripts/find_temporary_names.py db/marketshare.db
    python scripts/find_temporary_names.py db/marketshare.db --apply
"""
import argparse, collections, csv, pathlib, re, sqlite3

OVERRIDES = pathlib.Path(__file__).resolve().parents[1] / "data" / "boat_name_overrides.csv"


def words(s):
    return [w for w in re.split(r"[^a-z0-9]+", (s or "").lower()) if w]


def contains_words(long_w, short_w):
    if len(short_w) >= len(long_w):
        return False
    return any(long_w[i:i + len(short_w)] == short_w
               for i in range(len(long_w) - len(short_w) + 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--ratio", type=float, default=5.0)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    con = sqlite3.connect(a.db)
    rows = con.execute("""
        SELECT re.boat_id, b.boat_name, b.sail_no, re.boat_name_used, COUNT(*)
          FROM race_entries re JOIN boats b ON b.id = re.boat_id
         WHERE re.boat_name_used IS NOT NULL AND TRIM(re.boat_name_used) <> ''
         GROUP BY re.boat_id, re.boat_name_used""").fetchall()

    per = collections.defaultdict(list)
    for bid, rec, sail, used, n in rows:
        per[bid].append((rec, sail, used, n))

    confident, borderline = [], []
    for bid, lst in per.items():
        rec, sail = lst[0][0], lst[0][1]
        rec_w = words(rec)
        counts = collections.Counter()
        spelling = {}
        for _, _, used, n in lst:
            key = tuple(words(used))
            counts[key] += n
            # keep the most-used spelling of each name
            if key not in spelling or n > spelling[key][1]:
                spelling[key] = (used, n)
        rec_n = counts.get(tuple(rec_w), 0)
        for key, cnt in counts.items():
            if not key or list(key) == rec_w:
                continue
            if not contains_words(rec_w, list(key)):
                continue
            ratio = cnt / rec_n if rec_n else float("inf")
            row = (cnt, rec_n, bid, sail, rec, spelling[key][0], ratio)
            (confident if ratio >= a.ratio else borderline).append(row)

    confident.sort(key=lambda r: -r[0])
    borderline.sort(key=lambda r: -r[0])

    print(f"confident  {len(confident)}  (shorter name used >={a.ratio:g}x as often)")
    for cnt, recn, bid, sail, rec, short, ratio in confident[:18]:
        print(f"   {bid:<6} {rec!r:<34} -> {short!r}   ({cnt} vs {recn})")
    print(f"\nborderline {len(borderline)}  (left alone - decide these yourself)")
    for cnt, recn, bid, sail, rec, short, ratio in borderline[:10]:
        print(f"   {bid:<6} {rec!r:<34} vs {short!r}   ({cnt} vs {recn})")

    if not a.apply:
        print("\nDRY RUN - pass --apply to write the overrides")
        return

    existing = set()
    if OVERRIDES.exists():
        for r in csv.DictReader(OVERRIDES.open(encoding="utf-8-sig")):
            if (r.get("BoatId") or "").strip().isdigit():
                existing.add(int(r["BoatId"]))
    with OVERRIDES.open("a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        added = 0
        for cnt, recn, bid, sail, rec, short, ratio in confident:
            if bid in existing:
                continue
            w.writerow([bid, sail or "", short,
                        f"{rec} looks like a sponsor or one-off name: raced {cnt} "
                        f"times as {short} against {recn} as {rec}."])
            added += 1
    print(f"\nwrote {added} override(s) to {OVERRIDES}")
    print("Now run: python scripts/normalise_boat_names.py db/marketshare.db --apply")


if __name__ == "__main__":
    main()
