#!/usr/bin/env python3
"""One command: rebuild the dashboard and push it to everywhere people read it.

Colleagues who added a OneDrive shortcut to the shared file are reading a SYNCED
copy, not a snapshot - so replacing that one file in place is all a "push" has
to be. OneDrive carries it to them and their next open is current.

Two things this is careful about:

  * The filename never changes. Every shortcut and every Teams link anyone has
    already been sent points at that exact path; renaming the file silently
    breaks all of them.

  * The replace is atomic. A 3MB copy straight over a synced file can be read
    half-written by someone opening it at that moment, and by OneDrive itself,
    which would sync a truncated page. So it writes a temp file beside the
    target and then renames it, which on Windows is a single operation.

Destinations live in publish_targets.txt next to this script - one path per
line, # for comments - so adding a reader is editing one line, not the code.

    python scripts/publish.py --dry-run
    python scripts/publish.py
    python scripts/publish.py --no-build      # push the current build as-is
"""
import argparse, datetime, hashlib, json, os, pathlib, shutil, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
BUILT = ROOT / "dashboard" / "dashboard.html"
DB = ROOT / "db" / "marketshare.db"
DATA = ROOT / "dashboard" / "data.json"
TARGETS = pathlib.Path(__file__).resolve().parent / "publish_targets.txt"


def read_targets():
    if not TARGETS.exists():
        return []
    out = []
    for line in TARGETS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(pathlib.Path(os.path.expandvars(line)))
    return out


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]


def run(cmd):
    print("  $", " ".join(str(c) for c in cmd))
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        raise SystemExit(f"failed: {' '.join(str(c) for c in cmd)}")


def place(src, dst, dry):
    """Atomic replace: write beside the target, then rename over it."""
    if dry:
        was = f"{sha(dst)} {dst.stat().st_size:,}B" if dst.exists() else "absent"
        print(f"  would replace {dst}\n      now: {was}")
        return True
    if not dst.parent.exists():
        print(f"  SKIP  {dst}\n      parent folder does not exist - is OneDrive signed in?")
        return False
    tmp = dst.with_name(dst.name + ".pushing")
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)          # atomic on the same volume
    except OSError as ex:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        print(f"  FAIL  {dst}\n      {ex}")
        return False
    print(f"  ok    {dst}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-build", action="store_true",
                    help="push dashboard/dashboard.html as it stands")
    a = ap.parse_args()

    if not a.no_build:
        print("rebuilding from the database...")
        run([sys.executable, "scripts/export_dashboard_data.py", str(DB), str(DATA)])
        run([sys.executable, "scripts/build_dashboard.py"])
    if not BUILT.exists():
        raise SystemExit(f"no build at {BUILT} - run without --no-build")

    stamp = json.loads(DATA.read_text(encoding="utf-8"))["generated_at"] \
            if DATA.exists() else "?"
    print(f"\nbuild  {BUILT.stat().st_size:,} bytes  sha {sha(BUILT)}  generated {stamp[:19]}")

    targets = read_targets()
    if not targets:
        raise SystemExit(
            f"\nNo destinations. Add them to {TARGETS}, one path per line.")

    print(f"\npushing to {len(targets)} destination(s):")
    ok = sum(place(BUILT, t, a.dry_run) for t in targets)

    if a.dry_run:
        print("\nDRY RUN - nothing written")
        return
    print(f"\n{ok}/{len(targets)} updated.")
    print("OneDrive syncs it out from here - usually a minute or two. Anyone with")
    print("the page already OPEN keeps the old one until they reload it (Ctrl+F5).")
    print("The sidebar shows the build date, so a stale copy is visible at a glance.")


if __name__ == "__main__":
    main()
