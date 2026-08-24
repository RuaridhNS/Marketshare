#!/bin/bash
PY="/c/Users/ruari/AppData/Local/Python/bin/python.exe"
DB="db/marketshare.db"
for year in 2017 2018 2019 2020 2021 2022; do
  echo "=================== YEAR $year ===================" 
  "$PY" scripts/scrape_rorc_legacy.py "$DB" "$year" --delay 10
  echo
done
echo "=================== RORC FULL BACKFILL COMPLETE ==================="
