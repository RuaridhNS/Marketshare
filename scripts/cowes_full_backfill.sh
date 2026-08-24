#!/bin/bash
PY="/c/Users/ruari/AppData/Local/Python/bin/python.exe"
DB="db/marketshare.db"
for year in 2017 2018 2019 2021 2022 2023 2024 2025 2026; do
  echo "=================== YEAR $year ==================="
  "$PY" scripts/scrape_cowes_week.py "$DB" "$year" --delay 3
  echo
done
echo "=================== COWES WEEK FULL BACKFILL COMPLETE ==================="
