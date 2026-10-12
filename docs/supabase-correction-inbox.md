I verified everything load-bearing against the repo. Several claims from the designs and both judges turned out to be wrong, and one thing nobody looked at changes the plan. Here is the design.

---

# A shared correction inbox for the Solent market-share dashboard

**Status:** design, ready to build, with one gating test that has to pass first and one pre-existing breakage that has to be fixed first.
**Verified against:** `C:\Users\ruari\OneDrive - North Technology Group\NS_ISSP - General\Marketshare\project` at commit `71f56ad`, working tree clean, 2026-10-12.

---

## 1. What this is, and what it deliberately is not

Two people now make corrections to the same picture. Edits are staged in `localStorage`, which is per-browser, so neither can see the other's and only one of them can apply anything. The fix is a **shared inbox**: the dashboard posts the CSV ledger line it already generates to one Supabase table; a new first step in `refresh_all.py`'s `PIPELINE` drains that table into `data/*.csv` and the rest of the pipeline does what it already does.

Supabase is a **letterbox**, not a store. Nothing reads from it except one script on one machine. SQLite stays the system of record, the CSVs in git stay the record of human decisions, every apply script is untouched, and the page still works with no network at all.

**What it is not, and will not become:**

- **Not a read path.** The page never reads from Supabase. There is no `select` policy for `anon`, so a leaked key cannot read back even its own rows. This is the single most valuable property in the design and everything else is arranged around keeping it.
- **Not authentication.** Anyone holding the file can type any name. Attribution is a label for reading a `git diff`, not evidence.
- **Not an auto-applier of destructive decisions.** Boat merges go to a holding pen a human drains. Ownership timelines do not go through it at all (section 4 says why).
- **Not a replacement for the Copy-CSV path.** Every existing button on the Pending changes tab stays, works, and is still the entire path on a build with no inbox configured.

### The honest framing, from the ledgers themselves

I counted the rows:

| ledger | human decisions recorded, all time |
|---|---|
| `data/boat_merges.csv` | 95 |
| `data/segment_overrides.csv` | 48 |
| `data/boat_updates.csv` | **2** |

`boat_updates.csv` — the ledger this feature is mostly about, the one that carries sailmaker, owner, lead rep and contacted-by — has **two rows in its entire history**. The busy ledger is merges, and merges are the one thing I am deliberately *not* auto-applying.

So be clear-eyed about what is being bought. The value here is **visibility**: a correction made in a browser the owner cannot see becomes a line in a file the owner reads. The automation (boat updates and segments landing without anyone pasting) is a bonus on top of a thin flow. If corrections turn out to run at two a year, five days is too much and the honest answer is a "copy these rows and send them to Ruaridh" button. Section 8 prices both.

### The alternative nobody costed

`docs/HOSTING.md` already names this problem under *"Several people editing at once — not yet possible"* and proposes a **SharePoint list through Microsoft Graph**, at a day or two. It deserves a straight answer because it is the only option where **none of the commercially sensitive data leaves the Microsoft tenant** — which is what constraint 6 is really asking about.

Why I am not choosing it: Graph needs an access token. A static file has no way to get one without either an interactive login (which breaks "open the file, no install, no login" for the staging path — readers would be unaffected, but the person correcting would have to sign in) or an app registration with a client secret baked into a file on SharePoint, which is strictly worse than an anon key because a client secret is not designed to be public. The day-or-two estimate in `HOSTING.md` does not include that problem.

**But ask before creating a Supabase project.** "Is North Sails content for customer names, owner names and lead-rep names to sit in a US or EU third-party cloud?" is a sign-off question, not a technical one, and if the answer is no, the Graph route with a login on the staging path is the design to write instead. That conversation may take longer than the build.

---

## 2. The table schema and RLS policies

One project, **London region (`eu-west-2`)**. Owner names and rep names are personal data about identifiable third parties; picking the region is a two-second decision at creation time and impossible to change afterwards.

```sql
-- ============================================================================
-- Marketshare correction inbox.
-- Two tables, one trigger, one policy. Paste whole into the SQL editor of a
-- NEW Supabase project in the London region.
--
-- This is a letterbox. Rows arrive from dashboard.html, scripts/pull_inbox.py
-- drains them into data/*.csv on the owner's machine, and the CSV in git is
-- still the record of what was decided. Nothing reads this table but that
-- script.
-- ============================================================================

create table public.inbox (
  id            bigint generated always as identity primary key,
  ledger        text        not null,
  csv_row       text        not null,   -- ONE line, exactly as the page builds it
  author        text        not null default '',
  client_id     uuid        not null,   -- per browser profile, self-asserted
  page_built    text        not null default '',  -- the page's BUILT_AT stamp
  content_hash  text,                   -- md5(csv_row), set by the trigger
  created_at    timestamptz not null default now(),  -- overwritten by the trigger
  consumed_at   timestamptz,            -- set by the puller, never by a client
  outcome       text,                   -- 'appended' | 'replaced line 31' | 'rejected: ...'

  constraint inbox_ledger_ck check (
      ledger in ('boat_updates','segment_overrides','boat_merges')),

  -- One line, no embedded newlines, never a comment line. The CR/LF ban
  -- matters: apply_segment_overrides.py reads its ledger line-by-line and
  -- then CSV-parses each line on its own, so a multi-line field would not
  -- just inject a row, it would corrupt the parse. The '#' ban matters
  -- because merge_boats.py reads boat_merges.csv with a bare csv.DictReader
  -- and no comment filtering, so a '#' line there becomes a data row.
  constraint inbox_row_ck check (
      csv_row = ''                                  -- '' means scrubbed
      or (length(csv_row) between 3 and 2000
          and strpos(csv_row, E'\n') = 0
          and strpos(csv_row, E'\r') = 0
          and left(ltrim(csv_row), 1) <> '#')),

  constraint inbox_author_ck  check (length(author) <= 60),
  constraint inbox_built_ck   check (length(page_built) <= 40),
  constraint inbox_outcome_ck check (outcome is null or length(outcome) <= 300)
);

-- PER CLIENT, not global. Two people independently making the SAME correction
-- produce two rows, which is agreement and worth seeing in the run log. A
-- global unique index would also give a key-holder a 409-vs-201 oracle on
-- whether a given exact row had been staged; keyed on client_id they can only
-- probe their own history, so there is nothing to learn.
create unique index inbox_dedupe_ix on public.inbox (client_id, content_hash);
create index inbox_open_ix on public.inbox (created_at) where consumed_at is null;


-- ----------------------------------------------------------------------------
-- Limits and the kill switch as DATA, so stopping abuse is one UPDATE in the
-- SQL editor: no rebuild, no republish, no key rotation, no waiting for
-- OneDrive to carry a new file to anyone.
-- ----------------------------------------------------------------------------
create table public.inbox_guard (
  id              boolean primary key default true check (id),
  accepting       boolean not null default true,
  per_client_hour int     not null default 60,
  global_hour     int     not null default 600,
  max_open        int     not null default 5000
);
insert into public.inbox_guard default values;


-- ----------------------------------------------------------------------------
-- The server owns every field a client must not control, and counts.
-- A policy is a predicate, not a counter, which is why the rate limit lives
-- here and not in RLS.
--
-- security definer: the function runs as its owner (postgres), which holds
-- BYPASSRLS, so the SELECTs below see the table even though anon cannot.
-- VERIFY THIS WITH A TEST INSERT - a silently failing guard is worse than none.
--
-- service_role bypasses RLS but NOT triggers. pull_inbox.py only ever UPDATEs,
-- never INSERTs, so this never fires against it.
-- ----------------------------------------------------------------------------
create or replace function public.inbox_guard_fn() returns trigger
language plpgsql security definer set search_path = public as $$
declare g public.inbox_guard%rowtype;
begin
  select * into g from public.inbox_guard where id;

  if not g.accepting then
    raise exception 'inbox: not accepting submissions';
  end if;
  if (select count(*) from public.inbox where consumed_at is null) >= g.max_open then
    raise exception 'inbox: queue full';
  end if;
  if (select count(*) from public.inbox
        where client_id = new.client_id
          and created_at > now() - interval '1 hour') >= g.per_client_hour then
    raise exception 'inbox: too many submissions from this browser in the last hour';
  end if;
  if (select count(*) from public.inbox
        where created_at > now() - interval '1 hour') >= g.global_hour then
    raise exception 'inbox: too many submissions in the last hour';
  end if;

  new.content_hash := md5(new.csv_row);
  new.created_at   := now();     -- SERVER clock. A laptop with a wrong clock
  new.consumed_at  := null;      -- cannot reorder anything.
  new.outcome      := null;
  return new;
end $$;

create trigger inbox_guard_t before insert on public.inbox
  for each row execute function public.inbox_guard_fn();


-- ----------------------------------------------------------------------------
-- RLS. The anon key may ADD a row and do nothing else, anywhere.
-- ----------------------------------------------------------------------------
alter table public.inbox       enable row level security;
alter table public.inbox_guard enable row level security;

revoke all on public.inbox       from anon, authenticated;
revoke all on public.inbox_guard from anon, authenticated;
revoke all on all routines  in schema public from public, anon, authenticated;
alter default privileges in schema public revoke all on tables   from anon, authenticated;
alter default privileges in schema public revoke all on routines from public, anon, authenticated;

grant usage on schema public to anon;

-- COLUMN-level insert. anon cannot even NAME consumed_at, outcome,
-- content_hash or created_at in a request body. This is stronger than a WITH
-- CHECK expression because there is no expression to get wrong.
grant insert (ledger, csv_row, author, client_id, page_built)
  on public.inbox to anon;

create policy inbox_anon_insert on public.inbox
  for insert to anon with check (true);   -- shape is enforced by CHECK + trigger

-- Deliberately NO select, update or delete policy, for anon or authenticated,
-- on either table. No policy means deny. service_role bypasses RLS entirely
-- and is how pull_inbox.py reads. public.inbox_guard has RLS on and ZERO
-- policies, so a key-holder cannot even read whether the kill switch is set.
```

**Retention.** `pull_inbox.py` runs this at the end of every `--apply` run. Consumed rows are **scrubbed, never deleted** — deleting a row resurrects it, because the page would re-POST it and the unique index would no longer reject it.

```sql
update public.inbox
   set csv_row = '', author = '', page_built = ''
 where consumed_at is not null
   and consumed_at < now() - interval '60 days'
   and csv_row <> '';
```

**Project settings that are not SQL and matter as much as the SQL.** Taking this list from the second design, because it closes the vectors RLS does not touch:

- Authentication → Providers: **every provider off**, "Allow new users to sign up" **off**. Otherwise the anon key creates users and burns the email quota. `verify_jwt` is not a defence — the anon key *is* a valid JWT.
- Storage: **zero buckets**. Realtime: publication empty. Edge Functions: none.
- API settings: Exposed schemas = `public, graphql_public`. Max rows = 100.
- Free plan, or paid with a spend cap. The Free plan cannot generate a bill; it stops.
- Prefer the newer publishable/secret key scheme if offered, so the anon key can be rotated without rotating `service_role` with it.

**Negative tests before writing a line of application code.** With the anon key, confirm each of these fails: `GET /rest/v1/inbox`, `PATCH`, `DELETE`, `GET /rest/v1/inbox_guard`, and an `INSERT` naming `consumed_at`. Confirm an `INSERT` of the five granted columns succeeds — this also settles whether `generated always as identity` under column-level grants needs anything extra (I believe it does not, identity columns are not `serial`, but it is a two-minute test and not worth taking on trust).

---

## 3. What changes in `dashboard/template.html`

All of it additive, around 150 lines, no new tab, no new wire format, no new library. `fetch` is native; `@supabase/supabase-js` is a CDN script and constraint 4 forbids it. A POST of our own data is not a CDN dependency — no third-party *code* enters the page, so nothing can change the page's behaviour from outside, and the call degrades to a no-op.

### 3.1 Config (insert just above `function lsSet` at line 845)

```js
// Where staged corrections are shared, when this copy was built with one.
// build_dashboard.py replaces the literal "/*__INBOX_CONFIG__*/null" with a
// {url, key} object read from the project .env. No .env, no substitution,
// INBOX stays null, and this page is exactly what it was before any of this
// existed - which is what makes the no-network case the floor rather than a
// fallback.
const INBOX = /*__INBOX_CONFIG__*/null;
```

In `scripts/build_dashboard.py`, do the substitution **immediately before `check_js(template)`** (which sits after the `BUILT_AT` replace and before the data replace). That ordering is the whole reason I am choosing this over the `<script type="text/plain">` placeholder the second design proposed: `check_js`'s regex is `r'<script(?![^>]*type="text/plain")[^>]*>(.*?)</script>'`, so a `text/plain` block is never parsed — the config would escape the one guard that has already caught three silent page-killing edits. Substituting an inline literal before `check_js` means `node --check` parses the real thing. `node v24.16.0` is on PATH on this machine, so the check actually runs.

If the config ever needs to carry something that is not JS-safe, switch to the `text/plain` block then. It does not today: a Supabase URL and a base64url JWT.

Read the key from `.env` (already in `.gitignore`, line 5 — verified). Absent `.env`, substitute `null` and print `no inbox config - this build stages to this browser only`. Print the key's first eight characters so a stale or rotated key is visible at build time.

### 3.2 Identity (beside `PENDING_KEY`, line 905)

Add an `lsGet(key, fallback)` beside the existing `lsSet` — the inline `try/JSON.parse/catch` pattern already appears six times and these add three more.

```js
const AUTHOR_KEY = 'marketshare_author_v1';   // the name someone typed
const CLIENT_KEY = 'marketshare_client_v1';   // crypto.randomUUID(), once
const SENT_KEY   = 'marketshare_inbox_sent_v1'; // {"<ledger>|<hash>": true}
```

Every one must have a module-level variable fallback, because in a sandboxed SharePoint/Teams preview with an opaque origin **every `localStorage` read and write throws** — the comment at line 840 documents this and `lsSet` at 845 exists because an unguarded write once took the rest of a click handler with it. There the name is re-asked each session and the client id is regenerated per session. Both degrade; neither blocks the send.

### 3.3 Three row builders — a pure refactor, and one live bug fixed

Extract one-row functions from the three places that already build these exact lines, and rewrite the three `*ToCSV` functions to call them. The Copy-CSV path and the inbox then use the same code and cannot drift.

| new function | extracted from | verified against |
|---|---|---|
| `boatEditRow(boatId)` | the `forEach` body of `pendingEditsToCSV`, line 947 | `data/boat_updates.csv` header, exact, all nine columns including `NewPartialSailmaker` |
| `segmentRow(regattaId)` | the `lines` map in `renderPendingSegments`, line 2463 | `data/segment_overrides.csv` header `Pattern,Segment,Notes` |
| `mergeRow(m)` | the `forEach` body of `pendingMergesToCSV`, line 1094 | `data/boat_merges.csv` header `KeepSailNo,MergeSailNo,OnlyNamed,Notes` |

Move `esc_re` (currently local to `renderPendingSegments`) to module scope.

**Fix while you are in there.** Line 2463 is:

```js
return `^${esc_re(r.name)}$,${PENDING_SEG[id]},"Set from the dashboard."`;
```

`esc_re` escapes `[.*+?^${}()|[\]\\]` — **not the comma** — and the pattern field is never passed through `csvEsc`. Two regattas in the database have commas in their names (`Helly Hansen Sailing World Regatta Series, Detroit` and `Lightning Womens, Juniors, and Masters North American Championships`). Staging a segment on either produces a row that `apply_segment_overrides.py` parses as `Pattern=^Helly Hansen Sailing World Regatta Series`, `Segment= Detroit$`, prints `skipping: ' Detroit$' is not a segment`, and moves on. The correction is in the ledger, in git, and silently never applied. That is the project's signature failure mode, live today on the copy-paste path. `segmentRow` must wrap the pattern in `csvEsc` — `apply_segment_overrides.py` reads each line with `csv.reader`, so quoting is handled correctly on the other end.

### 3.4 `inboxSend` — fire and forget, never awaited, never throws

```js
async function inboxSend(ledger, row) {
  if (!INBOX || !row) return;
  if (!AUTHOR) { queueForName(ledger, row); return; }   // one-line banner asks
  const k = ledger + '|' + row;
  if (SENT[k]) return;
  try {
    const res = await fetch(INBOX.url, {
      method: 'POST',
      headers: { apikey: INBOX.key, Authorization: 'Bearer ' + INBOX.key,
                 'Content-Type': 'application/json', Prefer: 'return=minimal' },
      body: JSON.stringify({ ledger, csv_row: row, author: AUTHOR,
                             client_id: CLIENT_ID, page_built: BUILT_AT })
    });
    if (res.ok || res.status === 409) markSent(k);  // 409 = already there
    else setInboxState('could not share (' + res.status + ')');
  } catch (e) { setInboxState('offline'); }
}
```

`Prefer: return=minimal` is required, not stylistic: with no `select` policy, `return=representation` would fail.

The edit is **already saved** by the existing `localStorage` write before this runs. The network call is how the other person gets to see it, never how it is kept.

### 3.5 When it fires — four existing call sites, no new button

Immediately after the existing save in each of:

| function | line | sends |
|---|---|---|
| `setPendingEdit` | 912 | `inboxSend('boat_updates', boatEditRow(boatId))` |
| `setPendingSegment` | 868 | `inboxSend('segment_overrides', segmentRow(regattaId))` |
| `addPendingMerge` | 1070 | `inboxSend('boat_merges', mergeRow(m))` |
| `setPendingMergeOnlyNamed` | 1076 | `inboxSend('boat_merges', mergeRow(m))` |

No "share" button. The failure being fixed is a step people forget; adding a step is the wrong shape.

**No long debounce is needed, and I checked why.** The second design specified a three-second debounce on the grounds that typing a sailmaker name would send eight intermediate states. It would not: every `setPendingEdit` call site is a `change` on a `<select>` (line 1902) or a Save button (lines 2390, 7308). Nothing is bound to `input`. A **one-second trailing debounce per `(ledger, key)`** is still worth having, because a person can flick through a select a few times — but it is a politeness, not a flood control, and it must not be long enough that closing the tab loses the send.

### 3.6 Boot flush

At the end of the async IIFE, after `navigate()` (line 7333) — `boatsById` and `regattasById` must exist before the row builders can run. Re-send everything still **outstanding** and not in `SENT`, reusing the definitions the sidebar badge already uses: `!pendingApplied(b, PENDING[id])` for edits, `outstandingSegmentIds()` for segments, `boatsById[m.mergeId]` for merges.

This is what makes "staged on the train" eventually arrive, and it is also why months of already-applied edits sitting in the owner's browser are **not** flooded into the inbox: they are not outstanding.

### 3.7 The Pending changes tab — one sentence and one input

In `renderPendingChanges` (line 2498) and wired in `attachPendingChangesHandlers` (line 2637):

- A status line in the subtitle: *"Shared with the refresh — 3 sent, 1 waiting for a connection"* / *"offline, will send when you next open this"* / *"Not shared: this copy has no inbox configured — copy the CSV as before."*
- `<input id="inbox-author">` for the name. Changing the name does **not** re-send what is already sent; it applies to what you stage next.
- One sentence under the Notes input: *"this note is shared — keep it about the boat."*
- One sentence in the merges card: *"Merges are shared for review, not applied automatically — they need a person."*

Every existing button — Copy CSV, Download, Copy rows, Discard, Clear applied — stays, works, and is the whole path when `INBOX` is null.

**The one case that is worse than today, and must be said on screen:** in a sandboxed preview where `localStorage` throws *and* the network fails, the edit exists nowhere. The page must detect both failing and show the CSV row inline with *"copy this now — it is not saved anywhere."* Today that case loses the edit in silence, so this is a repair, not a regression.

### 3.8 What does not change

No network call on load. No CDN. No read from Supabase. No behaviour difference when `INBOX` is null. `scripts/publish.py` untouched.

---

## 4. `scripts/pull_inbox.py`

~250 lines. Uses `requests` (2.34.2, already imported by eight scrapers) and stdlib `csv`. No new dependency.

```
python scripts/pull_inbox.py db/marketshare.db            # dry run, prints what it would write
python scripts/pull_inbox.py db/marketshare.db --apply
```

### Credentials

`%USERPROFILE%\.marketshare\inbox.json` — `{"url": "...", "service_key": "..."}` — falling back to `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` in the environment.

**Not the project `.env`.** I verified this and it is the single most consequential placement decision in the design. The project lives at `C:\Users\ruari\OneDrive - North Technology Group\NS_ISSP - General\Marketshare\project`, and `scripts/publish_targets.txt` line 1 publishes the dashboard to `...\NS_ISSP - General\Marketshare\dashboard.html` — the **parent folder**. The repo is a subfolder of the SharePoint group library that distributes the dashboard. `.gitignore` stops git; it does nothing about OneDrive. A `service_role` key in `.env` or in `secrets/` would sit one folder away from the file being handed to colleagues, and `service_role` bypasses RLS completely — it is the one credential that undoes every protection in section 2.

`C:\Users\ruari\.marketshare\` is at the profile root and is not synced. Note that Known Folder Move is active on this machine (`C:\Users\ruari\Desktop` does not exist — it has been redirected into OneDrive), so "put it on your desktop" would be exactly wrong. The profile root is right.

If no credentials are found: print `no inbox configured` and **exit 0**. A fresh clone, a second machine, or a decision to stop using Supabase leaves the pipeline working exactly as it does today. The system has to work with no Supabase at *both* ends, not just in the browser.

### What it does, in order

**1. Fetch.** `GET {url}/rest/v1/inbox?consumed_at=is.null&order=created_at.asc&limit=1000` with the service key. Configured but unreachable → print loudly and **exit 1**. A refresh that silently dropped a colleague's corrections is a failed refresh, and `refresh_all` already turns a non-zero step into a non-zero overall exit.

**2. Per-ledger spec** — the only new knowledge in the system:

```python
LEDGERS = {
  "boat_updates": dict(
      path="data/boat_updates.csv",
      header="SailNo,BoatName,NewSailmaker,NewPartialSailmaker,NewOwner,"
             "NewTCC,NewLeadRep,NewContactedBy,Notes",   # 9 cols, verified exact
      key=lambda r: r[0].upper(), note_col=8, mode="append"),
  "segment_overrides": dict(
      path="data/segment_overrides.csv",
      header="Pattern,Segment,Notes",
      key=lambda r: r[0], note_col=2, mode="segment"),
  "boat_merges": dict(
      path="data/inbox_proposals.csv",            # HOLDING PEN, not the ledger
      header="ReceivedAt,Author,InboxRef,KeepSailNo,MergeSailNo,OnlyNamed,Notes",
      key=lambda r: (r[0].upper(), r[1].upper()), note_col=3, mode="propose"),
}
```

**3. Validate.** Every rejection writes its reason to `outcome` and marks the row consumed, so it is visible in the inbox and never retried.

- `next(csv.reader([csv_row]))` must yield exactly the header's column count.
- No field may contain CR or LF; field 0 must not start with `#`.
- `boat_updates`: `SailNo` must resolve (see step 4); `NewTCC` must `float()` if present.
- `segment_overrides`: `Pattern` must `re.compile`; every `|` part of `Segment` must be in `SEGMENTS`, **imported from `apply_segment_overrides`** so there is one definition of the seven segment names.
- `boat_merges`: both sail numbers must resolve and must differ.

**4. Resolve stale sail numbers through `boat_sail_aliases`** — taken from the third design, and it checks out: the table exists (`alias_sail_no TEXT PRIMARY KEY, boat_id`, 211 rows), `merge_boats.py:277` writes to it on every whole-record fold, `split_boat.py:120` removes it on an undo, and `boats.sail_no` is `UNIQUE`. A correction staged on a three-week-old copy of the file against a sail number that has since been merged away is **re-pointed at the survivor**, with `resolved from GBR3110` added to the note, instead of being rejected at the door. A colleague's copy is always stale by definition, so this is not an edge case.

**5. Collapse, and do not supersede across people.** Both judges disagreed here, and judge 1 is right. I read `apply_boat_updates.py`: it uses `csv.DictReader` with `.get()`, iterates rows in **file order**, and applies each `New*` column **independently**, with blank meaning "no change". So two rows for the same `SailNo` both apply.

- Within one `client_id`: keep the newest by server `created_at` (a re-stage, not a disagreement). The loser gets `outcome='superseded by #N'`.
- Across different `client_id`s: **append both**, in `created_at` order. The later row naturally wins on any column they both set, and nobody's reason text is destroyed. When two authors set the *same column* to *different values*, print `CONFLICT` naming both authors and both values in full. Two people disagreeing about a customer's sailmaker is exactly what a summary must not swallow.

**6. Write.** Atomic in every case: write `data/x.csv.pulling`, `flush()`, `os.fsync()`, `os.replace()` — the discipline `publish.py` already uses, and it matters more here because `data/` is inside a OneDrive-synced tree. Retry a sharing violation three times with a short backoff before failing the step.

- **`append` (boat_updates):** append at end of file. Skip if an existing line has the same key and identical non-Notes fields (`outcome='already in ledger'`). Note the comment block in `boat_updates.csv` sits *before* the header — `apply_boat_updates.py` strips `#` lines then `DictReader`s — so end-of-file is correct.

- **`segment` (segment_overrides):** this is the fiddly one and both the winning design and judge 1 got it wrong. `apply_segment_overrides.py:` `hit = next((r for r in rules if r[0].search(name or "")), None)` — **first match wins**, and the live ledger is built on broad patterns (`cape ?31`, `^royal southern`, `^rorc\b`, `^jog\b`, `rorc.*middle sea`) whose own notes argue explicitly about ordering. A corrected `^Royal Southern May Regatta$` appended at the bottom is shadowed and dead.

  The third design's rule is close, but "refuse when an existing unanchored pattern also claims the name" would refuse almost every Solent regatta. Here is the exact rule, which is better than any of the three because the script has the database and can simply check:

  ```
  Compile every existing pattern. For each, count how many of the 412 rows in
  `regattas` it matches.

  (a) An existing pattern that matches THIS regatta and NOTHING ELSE is a rule
      ABOUT this regatta. Replace that line in place; keep the old segment
      value and both reasons in the note.
          outcome = 'replaced line 31'
  (b) Otherwise, insert the new anchored rule IMMEDIATELY ABOVE THE FIRST
      non-comment, non-header line - i.e. at the top of the rule block, below
      the '#' comments. It matches exactly one regatta, so it changes nothing
      else, and first-match-wins makes it beat every blanket rule.
          outcome = 'inserted at top of rules'
  ```

  Note `segment_overrides.csv` is laid out the other way round from `boat_updates.csv`: the header is line 1 and the 19 comment lines follow it. The insertion point is after the last of those.

  This is a deliberate reversal of precedence for that one event — a human naming one regatta exactly now beats `cape ?31`. I think that is right, because an exact name is a decision and a pattern is a guess, but it is a judgement and the run must print it: `SEGMENT: ^Royal Southern May Regatta$ -> Grand Prix, inserted above 'cape ?31'`.

- **`propose` (boat_merges):** writes to `data/inbox_proposals.csv`, which **no apply script reads**. Taken from the third design, and I am taking it despite the winning design appending straight to `boat_merges.csv`, because `merge_boats.py` `DELETE`s a boat row and moves its history. It has two real refusals — same-season name clash, and sharing no raced name and no owner — but I read `fold()` and **both are skipped when `OnlyNamed` is set**, and `OnlyNamed` is a free-text field the page lets anyone type. The template's own comment records a Swan 53, a Class40 and a Botin 56 coming within one run of being absorbed. A scheduled job at six on a Monday morning does not get to make that call.

  The cost is one manual step: the owner copies four columns out of the proposals file into `boat_merges.csv`. That is exactly what they do today when pasting from the dashboard, and the colleague's merge has *still* become visible, which is the problem this exists to fix.

**7. Attribute, in the puller, not in the page.** Re-parse the row with `csv.reader`, append ` — <author> [inbox:<first 8 of content_hash>]` to the note column, re-emit with `csv.writer`. Keeping this out of the page means `boatEditRow()` produces one string whether it is copied to the clipboard or posted, so the two paths cannot drift, and the inbox ref is in the ledger where the dedupe check can find it. All three apply scripts ignore that column, so none of them learns anything new.

**8. Mark consumed**, one `PATCH` per row (rows are tens, not thousands), then run the 60-day scrub.

**9. Print.** The run detail goes to stdout; the **last three non-empty lines are the counts**, because `refresh_all.run()` does `tail = [l for l in (r.stdout or "").splitlines() if l.strip()][-3:]`. Verified. Which also means: do not promise anyone the full appended list is in the scheduler log — it is not, three lines per step is all that gets there. The review gate is `git diff data/`, and the step should say so.

### Where it goes in `PIPELINE`

**First**, above `("Repair mojibake", ...)`, with a comment in the house style:

```python
# FIRST, because everything below reads the files this writes. The dashboard
# is a static file: it cannot write to data/ or to the database, but it CAN
# post a ledger row to a shared inbox, and this is the step that drains that
# inbox into the ledgers. Pulled after "Merge boats", "Apply boat corrections"
# or "Apply segment overrides", a colleague's correction waits a whole week -
# and a correction nobody can see is the thing this exists to fix.
#
# It writes no table; the db argument is so that a row naming a sail number
# that no longer exists can be re-pointed through boat_sail_aliases, or
# rejected at the door where the reason is recorded, instead of being silently
# skipped by apply_boat_updates.py eighteen steps later. Boat merges are
# written to data/inbox_proposals.csv and NOT applied: merge_boats.py deletes
# a record, and its two guards are both bypassed when OnlyNamed is set.
("Pull correction inbox", ["pull_inbox.py", "--apply"], "inbox"),
```

`run()` builds `[PY, script, db] + argv[1:]`, which gives exactly `pull_inbox.py <db> --apply`. Verified against the three existing steps that take flags.

### Why running it twice is a no-op

Four independent reasons, any one sufficient:

1. **Only `consumed_at IS NULL` rows are selected,** and the script sets `consumed_at` in the same run. A second run selects nothing and writes nothing. Run the pipeline twice: `git diff data/` is empty the second time.
2. **Order is write → fsync → mark consumed.** If the run dies between them the next run re-reads the same rows, and the `[inbox:<hash>]` tag already in the ledger line makes every append a no-op. The crash window cannot produce a duplicate line. The reverse window cannot occur, by that ordering. Delivery is at-least-once on purpose, deduped at the file.
3. **The page cannot resurrect a consumed row.** The unique index on `(client_id, content_hash)` returns 409 and nothing is re-consumed — which is why consumed rows are scrubbed rather than deleted. A row the owner deliberately *deletes* from a ledger stays deleted.
4. **The backstop:** the apply scripts are already idempotent. `apply_boat_updates.py` is `UPDATE`/`INSERT OR REPLACE` against a unique index; `apply_segment_overrides.py` compares before writing and reports no change. Even a duplicated ledger line would be noise in a git diff, not drift in the database.

### Why ownership timelines are not in this, and a defect you should fix anyway

The second design flagged that `apply_owner_timeline.py` is not in `PIPELINE`. I verified it — and it is worse than that.

`apply_owner_timeline.py` does `DELETE FROM boat_owner_history WHERE boat_id = ?` — **every** row — then inserts the corrected rows with `confidence='manual'` and sets `boats.current_owner_id`. On the next refresh, `backfill_owners.py` (which *is* in `PIPELINE`) re-derives from `race_entries`, deletes only its own `SOURCE` rows, and inserts fresh ones. It then guards with `SELECT source FROM boat_owner_history WHERE boat_id = ? ORDER BY id DESC LIMIT 1` and only overwrites `current_owner_id` if that row is its own — but its rows were **just inserted**, so they always hold the highest `id`. The guard never fires. **A timeline correction's current owner is silently undone on every refresh.**

The manual history rows survive; the boat's current owner reverts. That is a fourth live instance of the exact failure the brief describes, found by reading, and it is worth half a day on its own merits: create `data/owner_timelines.csv`, give `apply_owner_timeline.py` a `--file` default, change the `backfill_owners.py` guard to check for *any* `confidence='manual'` row rather than the last-id row, and add the step to `PIPELINE` after "Apply owner corrections".

Until that is fixed, routing `owner_timeline` through the inbox would be building a conveyor belt to a broken machine. Phase 2, after the fix.

One smaller mismatch in the same family: `apply_boat_updates.py` reads `NewBoatCaptain` and `NewProgrammeManager`, but `pendingEditsToCSV()` does not emit those columns, so neither can currently be set from the page at all.

---

## 5. Identity and attribution

Self-asserted, and the design says so out loud rather than dressing it up.

There is no login and there cannot be one: constraint 3 says the page must work with no network, and an auth round trip at open time breaks that for every reader. So:

- **`author`** — a name typed once into the Pending changes tab, kept in `marketshare_author_v1`. The first `inboxSend()` with no name is *queued* rather than sent and a one-line banner asks for it; the queue flushes the moment a name is entered, or when the person clicks "send without a name". Nothing can be lost by queueing, because the edit is already in `PENDING`/`PENDING_SEG` and the flush just re-walks the outstanding items.
- **`client_id`** — `crypto.randomUUID()` in `marketshare_client_v1`. It separates two people who both type "Ruaridh", it is what the rate limit counts against, and it is what scopes the dedupe index so there is no probe oracle.
- **`created_at`** — set by the trigger from the server clock. A laptop with a wrong clock cannot reorder the collapse decision.
- **`page_built`** — the page's `BUILT_AT` stamp, so a row arriving from a three-month-old copy is identifiable, and so a validation failure can say *which* build produced it.

Attribution reaches the audit trail through the ledger's own free-text column — `Notes` on all three files — where `pull_inbox.py` writes ` — Ben S [inbox:4f3a2b]`. `git log -p data/boat_updates.csv` stays the audit trail it is today, now with a name on each line.

**What this is not: authentication.** Anyone holding the file can type any name. `author` is a convenience for reading a git diff, `client_id` tells browsers apart, `created_at` is the only fact the server owns. If a correction ever needs to be *defensible* rather than merely *attributable*, that is a different system and should be priced as one.

**I am deliberately not taking the allowlist** the second design proposed, where an unknown `client_id` is quarantined until the owner adds a line. Judge 1 is right about why: `client_id` lives in `localStorage`, which throws in the sandboxed Teams and SharePoint previews — the exact viewer this feature exists for. Their id regenerates every session, so every correction they ever make lands in quarantine and the allowlist can never catch up. The feature would do nothing for the person it is for. The control that replaces it is the one that already exists: every row lands in a git-tracked CSV with a name on it, and the owner reads `git diff data/` before committing.

---

## 6. Security: what a person holding the file can do

**Start from the fact.** The anon key is in a 3.8 MB file (`dashboard.html`, 3,768,444 bytes) on a SharePoint group library and in a Teams chat-files folder. It will be forwarded. It is a published identifier, not a secret.

**What the key can do:** POST rows to `public.inbox`. Up to 60 per hour per `client_id`, 600 per hour overall, 5,000 unconsumed in total. Each row at most 2,000 characters, one line, no CR or LF, not starting with `#`, into one of three named ledgers. That is the entire capability.

**What it cannot do, and what stops it:**

| | stopped by |
|---|---|
| Read the inbox back, **including its own rows** | RLS on, **no select policy exists** for `anon` |
| Read whether the kill switch is set | `inbox_guard` has RLS on and zero policies |
| Update or delete anything | no policies; column-level `GRANT` means `anon` cannot even *name* `consumed_at` or `outcome` |
| Forge a timestamp, mark something consumed, claim an outcome | `BEFORE INSERT` trigger overwrites all three |
| Probe whether a correction exists | the unique index is on `(client_id, content_hash)`, so a 409 only ever concerns the caller's own prior submission |
| Inject extra CSV rows via a multi-line payload | `CHECK` forbids CR/LF; the puller re-parses and re-counts columns anyway |
| Write a `#` comment into `boat_merges.csv` | `CHECK` forbids a leading `#`. `merge_boats.py:303` is a bare `csv.DictReader` with no comment filtering, so a `#` line there would become a data row — only `apply_boat_updates.py`, `apply_event_dates.py` and `apply_segment_overrides.py` strip them |
| Reach SQLite, the pipeline or the 40.9 MB database | the inbox is a table in a separate Postgres with no connection to any of it |
| Run SQL | PostgREST with no RPC functions exposed |
| Create accounts, burn the email quota, spend money | all auth providers off, sign-ups off, zero storage buckets, no edge functions, Free plan or a spend cap |

**What nothing stops:** a person inside the group writing a plausible, wrong correction — the right sail number, the wrong sailmaker. No technical control catches that, and claiming one would be a lie. The controls are human and already exist:

1. Every row lands in a git-tracked CSV with a name in its Notes column. `git diff data/` before committing is the gate.
2. `refresh_all.py` does **not** run `publish.py` — I checked. The pipeline rebuilds `dashboard/dashboard.html` locally; distributing it to the three targets in `publish_targets.txt` is a separate manual command. So a wrong correction can reach the owner's local build unreviewed, but it cannot reach a colleague without the owner typing `python scripts/publish.py`. That is a stronger review gate than the winning design claimed for itself.
3. `split_boat.py` sits directly after `merge_boats.py` in `PIPELINE` for exactly this, and merges do not auto-apply here anyway.

**Keys.**
- *anon key:* baked in by `build_dashboard.py` from the project `.env`. Rotating means rotate in Supabase, rebuild, publish — and `publish.py` replaces the file in place, so it reaches everyone within a OneDrive sync. Old copies get 401 and fall back to `localStorage`, which is today's behaviour, not a break.
- *service_role key:* `%USERPROFILE%\.marketshare\inbox.json`, outside the synced tree. See section 4.
- *Faster than rotation:* `update public.inbox_guard set accepting = false;` stops every insert in one second, with no rebuild, no republish and no waiting for a sync.

**What actually leaves the building.** Only the content of rows a person deliberately staged. For `boat_updates` that is sail number, boat name, sailmaker, partial sailmaker, owner name, TCC, **lead rep**, **contacted-by** and a free-text note. Owner names and lead reps are customer-identifying and are personal data about identifiable individuals; "contacted by" is commercial activity. So yes — sensitive data leaves, in a trickle, by deliberate act. Plausibly a few hundred rows a year, and the 60-day scrub is what stops those rows ever existing in one place.

I considered holding `leadRep` and `contactedBy` back, as the second design does. I am not doing it, for one reason: the Sales opportunities page is where lead rep and contacted-by are edited, and if those two fields silently take a different path from everything else on the same screen, people will not notice and will assume they shared. A feature that works for six fields and silently does not for two is worse than one that works for eight with a clear sentence about what is shared. If the data-protection answer comes back as "not those two", the right response is to drop them from the staging UI as well, not to split the path.

**What does not leave:** the database. All 140,925 race entries, 15,329 boats, 412 regattas, every market-share figure, the segment analysis, the sales-opportunities list, `boat_crm.in_cs`, the 23,715 IRC certificates, the globe. None of it is read by the send path and none can be fetched out of Supabase.

**And the uncomfortable thing to say out loud:** `dashboard.html` on SharePoint already contains every bit of that, gzipped into the page, and so does the whole repo — the project folder, including `db/marketshare.db`, sits *inside* the `NS_ISSP - General` library. This design does not change that exposure either way, and it is the larger number. Related, and I could not check it because I made no network calls: `data/boat_updates.csv` and `data/owner_corrections.csv` are tracked in git and pushed to `https://github.com/RuaridhNS/Marketshare.git`, carrying owner names, lead reps and contact notes. **If that repository is public, that disclosure dwarfs anything in this design and should be fixed before any of this is built.** Check it; it takes ten seconds.

---

## 7. Failure modes

| what happens | what the system does | what the user sees |
|---|---|---|
| **No network when staging** | `fetch` rejects inside its own try/catch; the `localStorage` write already happened; boot flush retries | *"1 waiting to share"*; nothing lost, no handler throws |
| **CORS refused** from `file://` (Origin: `null`) or a sandboxed preview | `fetch` rejects, permanently for that viewer | *"could not reach the inbox"*; Copy-CSV still works. **This is the gating unknown — see section 9** |
| **Supabase Free tier pauses** after ~7 days idle | 404/503, treated as offline | same as offline. Note the weekly refresh's GET is what keeps it awake, and the weekly refresh is currently broken (section 8) |
| **Anon key rotated, old copies circulating** | 401 on those copies, forever | *"could not share (401)"*; fixed when `publish.py` replaces the file in place |
| **Rate limit or kill switch** | trigger raises; PostgREST returns an error; treated as not-sent, retried at next boot | *"could not share"*. Caps an accidental loop. Does **not** stop a determined attacker, because `client_id` is self-asserted — the real control there is `accepting = false` and rotating the key |
| **Junk or hostile row** | puller rejects wrong column counts, unresolvable sail numbers, uncompilable patterns, unknown segment names, bad TCCs; reason recorded in `outcome`, row consumed | nothing reaches a ledger; the reason is in the inbox rather than silently skipped by an apply script eighteen steps later |
| **A plausible wrong correction** | nothing stops it | detection is `git diff data/`; recovery is deleting the line, committing, re-running. The row cannot reach colleagues until `publish.py` is run by hand |
| **Two people set the same column to different values** | both rows appended in server-clock order; later wins at apply time; `CONFLICT` printed with both authors and both values | no silent last-write-wins on a customer's sailmaker |
| **Two people set different columns on the same boat** | both appended, both apply — this is the normal case, not a conflict | both corrections land |
| **One person re-stages the same correction** | collapsed within `client_id`; loser marked `superseded by #N` | one ledger line |
| **A correction names a sail number merged away last week** | re-pointed via `boat_sail_aliases` onto the survivor, `resolved from GBR3110` in the note | the correction lands on the right boat |
| **Sail number resolves to nothing** | `outcome='rejected: GBR9999 not on file (page built 2026-07-14)'`, consumed, printed | visible in the run; never guessed at |
| **A segment rule would be shadowed** | puller inserts the anchored rule at the top of the rule block, or replaces an existing single-regatta pattern in place; prints which | the correction actually applies, which is the whole point |
| **Pull dies between writing and marking consumed** | next run re-reads the same rows; the `[inbox:hash]` tag makes every append a no-op | `git diff data/` empty on the second run |
| **Supabase unreachable during a refresh** | step exits 1, the remaining steps continue, `refresh_all` exits non-zero | corrections wait for the next run; the scheduler can alert — once the scheduler works |
| **No credentials on the machine** | `no inbox configured`, exit 0 | the pipeline, ledgers and dashboard are exactly what they are today |
| **OneDrive holds a ledger open, or makes a conflict copy** | `os.replace` fails; three retries; then the step fails and nothing is marked consumed; next run retries | real hazard, because `data/` is in the synced tree. A conflict copy of a ledger is worse than a failed write and should be looked for by hand if it happens |
| **`localStorage` throws AND the network fails** | the edit exists nowhere | the page must say so and show the CSV row inline: *"copy this now — it is not saved anywhere."* Today this loses the edit in silence |
| **The owner's browser holds months of old staged edits** | not flooded — the boot flush only sends items still outstanding by `pendingApplied()`, `outstandingSegmentIds()` and `boatsById[m.mergeId]` | nothing happens, which is correct |
| **A colleague's laptop clock is wrong** | irrelevant — `created_at` is set by the trigger | — |
| **Someone "tidies" `pull_inbox.py` out of `PIPELINE`** | every correction silently stops landing | no defence except the comment block above `PIPELINE`, which already says exactly this, and the script printing `inbox: N waiting` on every run it does make |

---

## 8. What it costs to build, and what it costs to own

### Before anything, two things that are not this feature

**0.5 day — fix the weekly refresh.** None of the three designs or either judge checked this, and it changes the premise. There *is* a scheduled task, `Marketshare weekly refresh`, Mondays 06:00. It ran today at 07:04:56 and returned `LastTaskResult: 1`. Its action is `C:\Solent Marketshare\marketshare_project\marketshare\scripts\refresh_all.cmd` — **that path does not exist**, and neither does its parent. `scripts/refresh_all.cmd` still sets `REPO` to the same dead path and has not been touched since 29 August; the repo has moved into OneDrive since. There is no `logs/` directory anywhere on the machine.

So the pipeline is not running unattended, and has not been for some time. A new pipeline step that drains a shared inbox is worth nothing until the pipeline runs. Fix `refresh_all.cmd`'s `REPO`, fix the task's `Execute` path, run it once by hand, confirm a log appears. Do this first whatever else happens — the inbox makes it more important, it does not create the need.

**0.5 day — the CORS spike.** See section 9. If it fails, the project is over, so it is day one, not day four.

### The build

| | days |
|---|---|
| Supabase project (London), table, guard, trigger, RLS, grants, the non-SQL lockdown list — **and the negative tests** that prove `anon` gets refused on SELECT, UPDATE and DELETE, not just that INSERT works | 0.5 |
| `template.html`: config placeholder, author/client identity, `lsGet`, the three row-builder extractions (plus the comma-quoting fix), `inboxSend`, sent-map, boot flush, status line, name input | 1.0 |
| `build_dashboard.py` substitution before `check_js`, `.env` reading, confirm `node --check` still passes on the unbuilt template | 0.25 |
| `pull_inbox.py`: three ledger specs, validation against the real `boats` table, alias resolution, the segment insertion rule, the proposals file, collapse and conflict reporting, atomic write, dry-run output, consume, scrub | 1.5 |
| Pipeline wiring, a full refresh run twice over, proving `git diff data/` is empty the second time | 0.5 |
| Docs: rewrite `docs/writing-to-the-database.md` (it currently recommends option A, a local server — this supersedes it), update the `HOSTING.md` section that says multi-user is not possible, header comments in the ledgers, README | 0.5 |
| **Total** | **4.25 focused, call it 5** |

**5 focused days, 6–8 elapsed.** Add 0.5 for the `apply_owner_timeline` fix if you want timelines in phase 2.

### What it costs to own, which nobody asked about and matters more

- One more thing that can break the Monday morning run, and it fails loudly by design (exit 1 on unreachable).
- A Supabase project that pauses after ~7 days idle on the Free plan. The weekly refresh keeps it warm — once the refresh works. Two weeks away and it sleeps; colleague edits queue locally and arrive when it wakes. The paid tier removes pausing; whether that is worth $25/month for two people is a judgement.
- A key to rotate when someone leaves, which is `publish.py` plus a sync.
- A queue to look at. The puller prints `inbox: N waiting, oldest 9 days` every run; somebody has to read it.
- `data/inbox_proposals.csv` becomes a file that has to be drained, and if nobody drains it the feature looks like it works and quietly does nothing. That risk is real and I have no automated answer to it beyond the staleness warning.
- The data-protection conversation, which is a calendar cost, not a day of work, and which may take longer than the build.

### The cheaper thing, honestly priced

Half a day: a **"Share these rows"** button on the Pending changes tab that copies the CSV rows plus a one-line "from Ben, 12 Oct" and tells you to paste them into the Teams chat. No Supabase, no key, no table, no new pipeline step, nothing leaves the tenant, and the ledger comment can stay exactly as it is.

What it does not do: it is a step people forget, which is the failure the inbox is for, and it does not work when someone makes a correction on a train and closes the tab. Given that `boat_updates.csv` has two rows in its history, this is a genuinely defensible answer and I would not argue hard against someone who picked it. The inbox earns its five days if corrections run weekly; it does not if they run quarterly. **Count the corrections made over the next month before committing the five days.** That is a cheaper experiment than the build.

---

## 9. The three things most likely to go wrong

**1. The CORS assumption, which every line of this rests on and I could not test.**

The page is opened as `file://` from a OneDrive-synced folder, so the browser sends `Origin: null`. The request is non-simple — the `apikey` and `Authorization` headers force a preflight. I believe PostgREST answers `Access-Control-Allow-Origin: *`, that a wildcard admits a null origin when credentials mode is not `include`, and that the POST goes through. I have not seen it work, and "I believe it works" is carrying far too much weight.

Three ways it could fail that I cannot rule out: a browser policy blocking `fetch` from `file://` outright; an Edge or Chrome enterprise policy on a managed North Technology Group device blocking `*.supabase.co`; and the Teams/SharePoint preview iframe, which is sandboxed with an opaque origin and may carry a `connect-src` CSP that forbids it. **The third is quite likely**, and it would mean the design works for people who open the synced file and not for people who click the preview — the same confusing split `docs/HOSTING.md` already warns about for the whole dashboard.

The test, first hour, before the schema: create a throwaway Supabase project with one permissive table, paste a key into a copy of `dashboard.html`, put it in the OneDrive folder, and POST one row from (a) Chrome opening the file from File Explorer, (b) Edge doing the same, (c) the Teams preview, (d) the SharePoint browser preview. Watch the network tab for the OPTIONS as well as the POST. If (a) and (b) fail, the project is over and the right answer is the local server already written up as option A in `docs/writing-to-the-database.md`. If only (c) and (d) fail, the design survives with a documented "open it, do not preview it" instruction — which is worth knowing before you build rather than after.

**2. The segment insertion rule — the one piece of new logic with no safety net.**

Everything else in this design fails loudly. A shadowed segment rule fails *silently*: the correction is in the inbox, consumed, in the ledger, in git, with a reason and a name on it — and `apply_segment_overrides.py` never reaches it, because something above it matched first. That is the exact class of bug the brief says has already happened three times, and my rule for avoiding it is the only part of this document that is genuinely new code rather than a rearrangement of things the repo already does.

I think the rule is right, and I think the "does this existing pattern match only this regatta?" test against the live `regattas` table is what makes it decidable rather than a guess. But it has a real edge I have not resolved: inserting an anchored rule at the top of the block reverses the deliberate precedence the file's own notes argue about (`cape ?31` above `^royal southern` is a decision somebody made and wrote down). I am asserting that an exact name beats a pattern. Mitigate it by making the run print every insertion with the rule it jumped over, and by checking the first few by hand. If you want one place to spend an extra half-day of testing, spend it here.

**3. The proposals file nobody drains, and the read-back I chose not to build.**

I decided that merges — 95 of the 145 human decisions ever recorded — do not auto-apply. And I decided the page never reads from Supabase, so neither person can see the other's *unapplied* staged list; visibility comes a refresh later, when the correction is in the data and `pendingApplied()` turns it green.

Both are defensible, and together they mean the thing the colleague most often wants to do produces a line in a file on the owner's machine that the colleague cannot see and nobody is obliged to act on. If the owner is slow, the colleague learns the queue does not work and goes back to not bothering — which is a softer version of the problem being solved.

I am not fixing this with a `select` policy. Granting `anon` any read hands every file-holder — including every stale and forwarded copy — a rolling window over owner names, lead reps and contact history, and that is the single most valuable property in the whole design. The fix that costs no policy change is `python scripts/pull_inbox.py db/marketshare.db` with no `--apply`: it prints exactly that list, with authors, dates and proposed merges. If the owner pastes that into Teams once a week, the visibility problem is solved at zero security cost.

If that turns out to be too manual, the next-cheapest step — half a day, and still no read policy — is to have `build_dashboard.py` bake the **current queue summary** into the page at build time, since the build machine already holds the service key. The page would show "3 corrections waiting as of this build, from Ben", as static text chosen by the build. That is publishing, not reading, and it is the right shape if the pull-script printout proves insufficient.

**Settle which of those three you want before anything is built**, because it is the difference between a safe design and a leaky one, and it is cheaper to decide now than to unpick a `select` policy later.