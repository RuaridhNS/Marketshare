#!/usr/bin/env python3
"""Bake dashboard/data.json into dashboard/template.html -> dashboard/dashboard.html
(a single self-contained file with no external dependencies).

The JSON is gzip-compressed and base64-encoded before embedding: at tens of
thousands of race-entry rows, raw JSON runs to tens of MB (mostly repeated key
names and string values), which blows past hosting size limits. Gzip shrinks
it ~15-18x; the page decompresses it client-side with the browser's native
DecompressionStream, so no external library is needed."""
import re
import os
import shutil
import tempfile
import subprocess
import json
import gzip
import base64
from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"
TEMPLATE = DASHBOARD_DIR / "template.html"
DATA = DASHBOARD_DIR / "data.json"
OUT = DASHBOARD_DIR / "dashboard.html"

# Characters that should never appear in source. A literal backspace or escape
# in the middle of a regex is not something anyone types on purpose - it is
# what is left after a shell ate the backslash in front of it.
CONTROL_CHARS = "\x00\x08\x0b\x0c\x1b"


def check_js(template):
    """Refuse to build a dashboard whose JavaScript does not parse.

    The whole page is one <script>, so one bad character disables the entire
    dashboard - and the failure is invisible from here, because the build
    writes its 2.5MB file just as happily either way. This has now happened
    three times, every time from a shell heredoc silently eating a backslash:
    a regex's word-boundary escape became a literal backspace, and the escaped
    apostrophe in "the race's own site" became a bare quote that closed its
    string early. That second one reached a published artifact before anyone
    noticed, which is what this function exists to stop.

    node does the parse when it is on PATH. When it is not, this says so
    rather than passing silently - a check you cannot see run is worse than
    no check, because it is trusted.
    """
    blocks = re.findall(r'<script(?![^>]*type="text/plain")[^>]*>(.*?)</script>',
                        template, re.S)
    js = "\n;\n".join(blocks)

    stray = sorted({c for c in js if c in CONTROL_CHARS})
    if stray:
        raise SystemExit(
            "template.html contains control character(s) "
            f"{[hex(ord(c)) for c in stray]} - almost certainly a backslash escape "
            "eaten by a shell heredoc. Re-apply that edit with the Write tool and a "
            "raw string rather than a heredoc.")

    if not shutil.which("node"):
        print("  ! node not on PATH - JavaScript NOT syntax-checked")
        return

    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(js)
            tmp = fh.name
        r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit("template.html JavaScript does not parse - refusing to "
                             "build:\n" + (r.stderr or r.stdout))
        print(f"  JavaScript parses ({len(js):,} chars in {len(blocks)} block(s))")
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


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

    check_js(template)

    out = template.replace("/*__DATA_JSON_B64GZ__*/", b64_str)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"Wrote {OUT} ({len(out):,} bytes; raw JSON was {len(data_str):,} bytes, "
          f"compressed {len(compressed):,} bytes)")


if __name__ == "__main__":
    main()
