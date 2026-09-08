#!/bin/bash
# Cowes Week standings backfill, for the seasons that publish no daily results.
#
#   scripts/cowes_points_backfill.sh              # all five, newest first
#   scripts/cowes_points_backfill.sh 2015 2007    # just these
#
# Newest first on purpose: 2018 and 2015 are the seasons a current market-share
# question is most likely to reach for, so they are worth having loaded before
# the run is an hour old.
#
# A season is roughly 45 class series plus 600-700 boat detail pages, one fetch
# each, so about 40 minutes at the default delay. --resume skips races that
# already carry entries, and every boat page is cached under data/cowes_boats/,
# so re-running this after an interruption costs the standings fetches again and
# almost nothing else. -u because this goes to a log file, where Python would
# otherwise block-buffer it and a wedged fetch would look like a healthy run.
PY="/c/Users/ruari/AppData/Local/Python/bin/python.exe"
DB="db/marketshare.db"
YEARS=("$@")
if [ ${#YEARS[@]} -eq 0 ]; then
  YEARS=(2018 2015 2009 2008 2007)
fi
for year in "${YEARS[@]}"; do
  echo "=================== YEAR $year ==================="
  timeout 7200 "$PY" -u scripts/scrape_cowes_points.py "$DB" "$year" --delay 3 --resume
  if [ $? -eq 124 ]; then
    echo "  !! TIMED OUT after 2h - re-run this script for $year to finish it"
  fi
  echo
done
echo "=================== COWES POINTS BACKFILL COMPLETE ==================="
