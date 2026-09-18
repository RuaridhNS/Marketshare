#!/usr/bin/env python3
"""Take a safe, consistent snapshot of the database.

Now that the project lives in a synced OneDrive folder this matters more than
it used to. A plain file copy of a SQLite database can catch it mid-write and
produce a snapshot that is quietly corrupt; so can OneDrive itself, uploading
while a script is writing. VACUUM INTO goes through SQLite, which takes a read
lock and writes a defragmented, internally consistent copy - safe even with the
database in use.

Snapshots land OUTSIDE the synced folder by default, because a backup that
syncs to the same place as the thing it is backing up is not a backup.

    python scripts/snapshot_db.py
    python scripts/snapshot_db.py --keep 10
"""
import argparse, datetime, pathlib, sqlite3, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DB = ROOT / "db" / "marketshare.db"
DEST = pathlib.Path(r"C:\Marketshare snapshots")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=str(DEST))
    ap.add_argument("--keep", type=int, default=14,
                    help="how many snapshots to retain (0 keeps everything)")
    a = ap.parse_args()

    dest = pathlib.Path(a.dest)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"marketshare-{datetime.datetime.now():%Y%m%d-%H%M%S}.db"

    con = sqlite3.connect(DB)
    bad = con.execute("pragma integrity_check").fetchone()[0]
    if bad != "ok":
        con.close()
        raise SystemExit(f"REFUSING to snapshot: integrity_check says {bad!r}.\n"
                         "Snapshotting a corrupt database just makes a corrupt "
                         "backup. Restore from an earlier snapshot instead.")
    # VACUUM INTO refuses to overwrite, which is what we want
    con.execute("VACUUM INTO ?", (str(out),))
    con.close()
    print(f"snapshot  {out}  ({out.stat().st_size / 1_048_576:.0f} MB)")

    if a.keep:
        snaps = sorted(dest.glob("marketshare-*.db"))
        for old in snaps[:-a.keep]:
            old.unlink()
            print(f"  pruned  {old.name}")
    print(f"\n{len(sorted(dest.glob('marketshare-*.db')))} snapshot(s) in {dest}")


if __name__ == "__main__":
    main()
