#!/bin/bash
PY="/c/Users/ruari/AppData/Local/Python/bin/python.exe"
DB="db/marketshare.db"
for year in 2018 2019 2020 2021 2022; do
  echo "=================== YEAR $year ==================="
  timeout 4200 "$PY" scripts/scrape_rorc_legacy.py "$DB" "$year" --delay 10
  if [ $? -eq 124 ]; then echo "  !! TIMED OUT after 70min - likely a network hang - moving to next year"; fi
  echo
done
echo "=================== RORC FULL BACKFILL COMPLETE ==================="
