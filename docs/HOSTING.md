# Hosting Marketshare for the organisation

## The short answer on SharePoint

**SharePoint will not run this dashboard as a web page.** Put `dashboard.html`
in a document library and clicking it downloads the file or shows a dead
preview — it does not execute.

That is not a configuration you can talk your way past. SharePoint Online
blocks custom script by default on every modern site, and Microsoft's own
documentation says that any exemption an admin grants is
*"overridden to Not allowed within 24 hours."* So even with a cooperative IT
department, the classic "enable custom script and drop the HTML in Site Assets"
route breaks itself by tomorrow.

SharePoint can still be the **front door**. It just cannot be the **host**.

---

## Four routes, cheapest first

### 1. Synced library — works today, no IT, no hosting

The single-file build is genuinely self-contained: one `.html`, no server, no
internet. So:

1. Put `dashboard.html` in a SharePoint document library.
2. Colleagues click **Sync** (or **Add shortcut to OneDrive**) on that library.
3. They open the file **from their synced folder in File Explorer**, not from
   the browser tab.

It opens in their browser off the local disk and every feature works. When you
publish a new build, OneDrive syncs it and their next open is current.

- **Good:** zero infrastructure, zero permissions work, works offline, and the
  library's existing permissions already control who sees it.
- **Bad:** they must open the *synced copy*. Opening it in SharePoint's web
  view will not work, and that is a confusing thing to have to explain.
- **Updates:** as live as your sync — minutes.

Put a `README.txt` next to it in the library saying "open this from your synced
OneDrive folder, not from this page", because someone will try.

### 2. Teams tab pointing at a real host — the pragmatic one

Host the split build somewhere that serves HTTPS, then surface it in Teams or
SharePoint with an **Embed** web part or a **Website** tab. The page is small
(300KB) and the data is a separate 2MB file, so a refresh only replaces the
data.

**Azure Static Web Apps** is the natural host in a Microsoft shop: there is a
free tier, it sits in your own tenant, and it supports Entra ID (the login your
colleagues already have) so the page is not public. Deploying is copying two
files.

Note the Embed web part only accepts domains on the tenant's **HTML Field
Security** allow-list, so IT has to add the host once. That is a five-minute
request, not a project.

- **Good:** a real URL, proper org sign-in, no "open from the synced folder"
  instructions, and it is where people already work.
- **Bad:** needs IT twice — an Azure subscription and one allow-list entry.
- **Updates:** upload a new `data.json.gz` whenever you like.

### 3. SPFx web part — the officially supported route

Wrap the dashboard in a SharePoint Framework web part and deploy it to the
tenant app catalog. This is the only way to put custom UI *inside* a modern
SharePoint page with Microsoft's blessing.

- **Good:** first-class SharePoint citizen, drops onto any page.
- **Bad:** needs a Node build chain, an app catalog, and someone to own it.
  Disproportionate for one dashboard.

### 4. Keep using the Claude artifact

The artifact link already works, is private, and can be shared inside the
organisation from its share menu. Nothing to host, nothing to ask IT for.
Worth keeping while you decide about the others.

---

## Building for a host

```bash
python scripts/export_dashboard_data.py db/marketshare.db dashboard/data.json
python scripts/build_dashboard.py            # dashboard/dashboard.html, one file
python scripts/build_dashboard.py --split    # dist/index.html + dist/data.json.gz
```

- **Single file** (3.0MB) — routes 1 and 4. Opens from disk.
- **Split** (300KB page + 2.0MB data) — routes 2 and 3. Both files must sit in
  the same folder and be served over http(s); the page fetches the data at
  load with a cache-buster, so a replaced data file is picked up immediately.
  It will *not* work opened straight off disk, and says so plainly if you try.

---

## "Live updates" — what that actually requires

Be clear about which of two things is meant, because they cost very different
amounts.

### Fresh data for readers — easy

The database is the source of truth and the dashboard is a snapshot of it.
Schedule the three commands above plus an upload, and every reader gets current
figures. Windows Task Scheduler or a GitHub Action both do this in a few lines.
Nothing in the dashboard needs to change.

### Several people editing at once — not yet possible

Today the dashboard stages edits **in the reader's own browser** and asks them
to export a CSV for someone to apply. Two people editing the same boat produce
two CSVs and one of them quietly loses. That is fine for one person maintaining
the record; it does not survive being shared with a team.

Making it multi-user needs a shared datastore behind the page. The natural one
here is a **SharePoint list** written through Microsoft Graph: the page would
read and write edits directly, the list would carry version history and the
permissions you already manage, and the nightly rebuild would fold those edits
into the database. That is real work — a day or two — but it is the thing that
turns this from a report into a system.

**Until that exists, share it read-only and keep one person applying edits.**
Sending the current build to a team and inviting everyone to edit will lose
data, silently.

---

## Duplicating it

Every build reads from `db/marketshare.db` and writes standalone files, so
"duplicate" is just "build again". For a per-team or per-region copy, filter at
export time rather than making a second database — one source of truth, many
views.
