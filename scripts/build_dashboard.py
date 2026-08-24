#!/usr/bin/env python3
"""Bake dashboard/data.json into dashboard/template.html -> dashboard/dashboard.html
(a single self-contained file with no external dependencies)."""
import json

TEMPLATE = "/home/claude/marketshare/dashboard/template.html"
DATA = "/home/claude/marketshare/dashboard/data.json"
OUT = "/home/claude/marketshare/dashboard/dashboard.html"

def main():
    with open(DATA) as f:
        data_str = f.read()
    # validate it's real JSON before embedding
    json.loads(data_str)

    with open(TEMPLATE) as f:
        template = f.read()

    if "/*__DATA_JSON__*/" not in template:
        raise SystemExit("Placeholder not found in template.html")

    out = template.replace("/*__DATA_JSON__*/", data_str)
    with open(OUT, "w") as f:
        f.write(out)
    print(f"Wrote {OUT} ({len(out):,} bytes)")

if __name__ == "__main__":
    main()
