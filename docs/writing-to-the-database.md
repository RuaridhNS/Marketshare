# Writing to the database from the dashboard

Written 30 Sep 2026, to settle how "upload an entry list" and "add a boat"
should actually work.

## The constraint, plainly

`dashboard.html` is one self-contained file. You open it from File Explorer,
it runs entirely in the browser, and it has nothing behind it. That is
deliberate — it is why a colleague can add a OneDrive shortcut and just open
the thing, with no install, no login and no server to keep alive.

It is also why the page cannot write to `db/marketshare.db`.

A web page has no access to the file system. It can read a file you hand it
through a file picker or a drop zone, and it can hand you a file back as a
download. It cannot open a path, and it certainly cannot open a 39 MB SQLite
file on a OneDrive share and modify it in place. No amount of JavaScript
changes this; it is the browser's security model, not a gap in the code.

So "write straight to the database" needs something listening on the other
end. The question is what, and what that costs.

## What happens today

Edits made in the page — a sailmaker changed on a boat, a segment set on a
regatta — are staged in the browser's `localStorage` under
`marketshare_pending_edits_v1` and `marketshare_pending_segments_v1`, shown
on the **Pending changes** tab, and exported as a CSV. A script then applies
that CSV to the database, and the next build carries the result back into the
page.

It works, it survives a refresh, and nothing changes the data without a person
deciding to run something. The cost is that round trip: export, run, rebuild,
publish.

## The three options

### A. A small local server (recommended)

One command on your machine:

    python scripts/serve.py

It serves the dashboard on `http://localhost:8800` and accepts writes straight
into `db/marketshare.db`. You open that URL instead of the file. Adding a boat
or uploading an entry list lands in the database as you do it — no staging, no
CSV, no second command.

The page would detect whether the server is there: served from localhost it
shows the write controls, opened from OneDrive it does not, and everything
else behaves as now.

- **Who can write:** you, on your machine, while the server is running.
- **Colleagues:** unchanged — they open the OneDrive file and read. They see
  your changes at the next publish.
- **Effort:** a day-ish. A small Python server, write endpoints for boats,
  entry lists and segments, and the page learning to use them when present.
- **Risk:** low. The server only runs when you start it. If it is not running
  the page falls back to exactly what it does now, so nothing you already rely
  on breaks.
- **Honest drawback:** it is a second thing to remember to start, and it only
  works where the database actually is. Not on your phone, not from the
  SharePoint link.

### B. Keep staging, make it one click

Leave the architecture alone and remove the friction instead: the page writes
its pending changes to a file, and a small watcher applies them and rebuilds
whenever that file changes.

- **Who can write:** anyone, into the staging file. It is still applied on your
  machine.
- **Effort:** half a day.
- **Risk:** very low; it is the current design with the manual step automated.
- **Honest drawback:** it is still not "straight to the database". There is a
  delay, and a failed apply is something you have to notice.

### C. A real web application

Move the dashboard onto a proper hosted app with a database behind it, so
everyone reads and writes the same live data from a URL.

- **Who can write:** whoever you allow, from anywhere, at the same time.
- **Effort:** weeks, and it never stops — hosting, authentication, backups,
  someone owning it.
- **Honest drawback:** it throws away the thing that makes this work today. A
  single file on OneDrive has no login, no downtime and no dependency on
  anyone keeping a service running. That is worth more than it sounds.

## Recommendation

**A.** It gives you what you asked for where it matters — at your desk, where
the data lives — without giving up the file-on-OneDrive distribution that makes
the dashboard usable by everyone else. B is the fallback if a day is too much
to spend. C is the right answer only if several people need to edit the same
data at once, which is not the case today.

## One thing to fix regardless

The entry-list upload parses `.xlsx` using a library fetched from a CDN at
page load:

    https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js

That contradicts the promise the build makes — "a single self-contained file
with no external dependencies" — and it means upload silently fails for anyone
offline, or on a network that blocks the CDN. It should be embedded in the page
the way the Proxima Nova font is, or replaced with a CSV path that needs no
library at all.
