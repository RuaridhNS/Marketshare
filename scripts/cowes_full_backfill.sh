#!/bin/bash
PY="/c/Users/ruari/AppData/Local/Python/bin/python.exe"
DB="db/marketshare.db"
for year in 2017 2018 2019 2021 2022 2023 2024 2025 2026; do
  echo "=================== YEAR $year ==================="
  timeout 1800 "$PY" scripts/scrape_cowes_week.py "$DB" "$year" --delay 3
  if [ $? -eq 124 ]; then echo "  !! TIMED OUT after 30min - likely a network hang - moving to next year"; fi
  echo
done
echo "=================== COWES WEEK FULL BACKFILL COMPLETE ==================="
