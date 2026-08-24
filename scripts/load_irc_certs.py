#!/usr/bin/env python3
"""
Load IRC_Certs_Database CSV (boat design + rating data, one row per
certificate) into the database as the authoritative TCC source, overriding
whatever TCC value came from race-results scraping.

A boat can have multiple certificates (trial runs, endorsed/in-force,
copies, archived/expired). Per sail number we pick the "best" one by
cert_type priority (ENDORSED > STANDARD > COPY > ARCHIVE > blank > TRIAL),
then most recent year.

Usage:
  python3 load_irc_certs.py <db.sqlite> <IRC_Certs_Database.csv>
"""
import sys
import csv
import argparse
import sqlite3
import datetime

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from build_db import norm

CERT_PRIORITY = {"ENDORSED": 0, "STANDARD": 1, "COPY": 2, "ARCHIVE": 3, "": 4, "TRIAL": 5}


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("csv_file")
    return p.parse_args()


def cert_sort_key(row):
    cert_type = (row.get("cert_type") or "").strip().upper()
    try:
        year = -int(row.get("year") or 0)
    except ValueError:
        year = 0
    return (CERT_PRIORITY.get(cert_type, 4), year)


def main():
    args = parse_args()
    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    with open(args.csv_file, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    by_sailno = {}
    for row in rows:
        sail_no = norm(row.get("sail_number"))
        if not sail_no:
            continue
        by_sailno.setdefault(sail_no, []).append(row)

    updated = 0
    not_found = []
    for sail_no, cert_rows in by_sailno.items():
        cert_rows.sort(key=cert_sort_key)
        best = cert_rows[0]
        try:
            tcc = float(best["tcc"])
        except (ValueError, TypeError, KeyError):
            continue

        cur.execute("SELECT id, tcc FROM boats WHERE sail_no = ?", (sail_no,))
        boat_row = cur.fetchone()
        if not boat_row:
            not_found.append(sail_no)
            continue
        boat_id, old_tcc = boat_row
        if old_tcc == tcc:
            continue
        cur.execute("UPDATE boats SET tcc = ?, updated_at = ? WHERE id = ?",
                    (tcc, datetime.datetime.now().isoformat(), boat_id))
        updated += 1

    conn.commit()
    print(f"Matched {len(by_sailno)} certificated boats against the DB. "
          f"Updated TCC for {updated} boat(s).")
    if not_found:
        print(f"{len(not_found)} sail number(s) in the certs file have no matching boat "
              f"in the DB (not yet in any loaded race/roster): {', '.join(sorted(not_found)[:20])}"
              + (" ..." if len(not_found) > 20 else ""))
    conn.close()


if __name__ == "__main__":
    main()
