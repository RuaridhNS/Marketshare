#!/bin/bash
PY="/c/Users/ruari/AppData/Local/Python/bin/python.exe"
export PYTHONUNBUFFERED=1
DB="db/marketshare.db"
for kw in may june july september; do
  echo "=================== KEYWORD $kw ==================="
  timeout 2400 "$PY" scripts/scrape_royal_southern.py "$DB" --keyword "$kw" --delay 4
  if [ $? -eq 124 ]; then echo "  !! TIMED OUT - moving on"; fi
  echo
done
echo "=================== RSYC MONTH BACKFILL COMPLETE ==================="
