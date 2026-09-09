#!/bin/bash
# RORC legacy archive backfill (rorc.org/raceresults/, 2007-2022).
#
#   scripts/rorc_full_backfill.sh              # every season the archive holds
#   scripts/rorc_full_backfill.sh 2011 2014    # just these
#
# --resume is always passed: it skips pages already fetched, judged by
# races.source_url and by the per-race CSV left in exports/. An audit on
# 2026-09-09 found 177 pages listed on the season indexes with nothing in the
# database - 72 of them in 2011 alone - because earlier runs covered only
# 2018-2022 and whatever was done by hand before that. Without --resume this
# would re-fetch all 1,491 pages at ten seconds each, which is four hours to
# collect what is already there.
#
# The delay is not tunable down: rorc.org/robots.txt declares Crawl-delay: 10
# and the site allows this crawler on that basis.
#
# 2010 is absent on purpose - its season index lists no race pages at all.
PY="/c/Users/ruari/AppData/Local/Python/bin/python.exe"
DB="db/marketshare.db"
YEARS=("$@")
if [ ${#YEARS[@]} -eq 0 ]; then
  YEARS=(2011 2014 2015 2016 2017 2018 2012 2021 2013 2022 2009 2019 2020 2007 2008)
fi
for year in "${YEARS[@]}"; do
  echo "=================== YEAR $year ==================="
  timeout 7200 "$PY" -u scripts/scrape_rorc_legacy.py "$DB" "$year" --delay 10 --resume
  if [ $? -eq 124 ]; then
    echo "  !! TIMED OUT after 2h - re-run this script for $year to finish it"
  fi
  echo
done
echo "=================== RORC BACKFILL COMPLETE ==================="
