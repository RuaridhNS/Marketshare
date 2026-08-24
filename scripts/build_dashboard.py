#!/usr/bin/env python3
"""Bake dashboard/data.json into dashboard/template.html -> dashboard/dashboard.html
(a single self-contained file with no external dependencies)."""
import json
from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"
TEMPLATE = DASHBOARD_DIR / "template.html"
DATA = DASHBOARD_DIR / "data.json"
OUT = DASHBOARD_DIR / "dashboard.html"

def main():
    with open(DATA, encoding="utf-8") as f:
        data_str = f.read()
    # validate it's real JSON before embedding
    json.loads(data_str)

    with open(TEMPLATE, encoding="utf-8") as f:
        template = f.read()

    if "/*__DATA_JSON__*/" not in template:
        raise SystemExit("Placeholder not found in template.html")

    out = template.replace("/*__DATA_JSON__*/", data_str)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"Wrote {OUT} ({len(out):,} bytes)")

if __name__ == "__main__":
    main()
