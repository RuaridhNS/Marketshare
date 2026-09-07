#!/bin/bash
# Cowes Week historical backfill, one season at a time.
#
#   scripts/cowes_full_backfill.sh              # every season the site publishes
#   scripts/cowes_full_backfill.sh 2007 2008    # just these
#
# --resume is passed always. A season is 250-380 fetches and the timeout below
# will cut a slow one off part way; without --resume the next attempt started at
# day 1 and spent its whole budget re-fetching what was already loaded (2016 got
# 105 races in and would have re-done all 105). With it, re-running this script
# is how you finish an interrupted season, so it is safe to run repeatedly.
#
# Seasons deliberately absent from the list: 2015, 2018 and 2020 publish no
# results at all - discover_races returns zero races for each. 2020 is the
# cancelled season; 2015 and 2018 are simply missing from the site, so their
# gap in the database is the source's, not ours, and re-running them forever
# would never fill it.
PY="/c/Users/ruari/AppData/Local/Python/bin/python.exe"
DB="db/marketshare.db"
YEARS=("$@")
if [ ${#YEARS[@]} -eq 0 ]; then
  YEARS=(2007 2008 2009 2010 2011 2012 2013 2014 2016 2017 2019 2021 2022 2023 2024 2025 2026)
fi
for year in "${YEARS[@]}"; do
  echo "=================== YEAR $year ==================="
  # -u because this output goes to a log file, where Python would otherwise
  # block-buffer it in 8KB chunks: a season that is running normally and one
  # that is wedged on a hung fetch look identical for twenty minutes.
  timeout 3600 "$PY" -u scripts/scrape_cowes_week.py "$DB" "$year" --delay 3 --resume
  if [ $? -eq 124 ]; then
    echo "  !! TIMED OUT after 60min - re-run this script for $year to pick up where it stopped"
  fi
  echo
done
echo "=================== COWES WEEK BACKFILL COMPLETE ==================="
