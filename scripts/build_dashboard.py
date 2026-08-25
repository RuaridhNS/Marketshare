#!/usr/bin/env python3
"""Bake dashboard/data.json into dashboard/template.html -> dashboard/dashboard.html
(a single self-contained file with no external dependencies).

The JSON is gzip-compressed and base64-encoded before embedding: at tens of
thousands of race-entry rows, raw JSON runs to tens of MB (mostly repeated key
names and string values), which blows past hosting size limits. Gzip shrinks
it ~15-18x; the page decompresses it client-side with the browser's native
DecompressionStream, so no external library is needed."""
import json
import gzip
import base64
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

    compressed = gzip.compress(data_str.encode("utf-8"), compresslevel=9)
    b64_str = base64.b64encode(compressed).decode("ascii")

    with open(TEMPLATE, encoding="utf-8") as f:
        template = f.read()

    if "/*__DATA_JSON_B64GZ__*/" not in template:
        raise SystemExit("Placeholder not found in template.html")

    out = template.replace("/*__DATA_JSON_B64GZ__*/", b64_str)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"Wrote {OUT} ({len(out):,} bytes; raw JSON was {len(data_str):,} bytes, "
          f"compressed {len(compressed):,} bytes)")

if __name__ == "__main__":
    main()
