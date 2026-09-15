# Task 41 — Calibre-Web-Automated deep dive: what should BookBrain borrow?

**Not started.** Run in its own fresh Claude Code session. This produces a
written assessment — it does **not** change any BookBrain code, and does
**not** install or run Calibre-Web-Automated anywhere near the real
BookBrain deployment or its real Drive/database.

James dropped a link to
[`crocodilestick/Calibre-Web-Automated`](https://github.com/crocodilestick/Calibre-Web-Automated)
(CWA) and wants a real look at it — not just the README skim this prompt is
based on, but the actual code, feature-by-feature, to see what's worth
stealing (ideas, architecture, or literal code where the license allows it)
for BookBrain.

**Don't re-cover ground already investigated.** CWA's own README lists
["Shelfmark"](https://github.com/calibrain/shelfmark) (`calibrain/calibre-web-automated-book-downloader`)
as an affiliated project — that's the book-downloader BookBrain already
researched and built its own native equivalent of (see
`prompts/README.md`'s entry on the acquisition-provider work; Anna's
Archive + Libgen providers exist because of that research). This prompt is
about **CWA itself** — the calibre-web-automated server — not the
downloader.

## What CWA actually is (confirmed by cloning it — don't take this on faith, re-verify)

A GPL-3.0-or-later fork of `janeczku/calibre-web` (itself a Flask app,
package `cps/`), aiming to be "an all-in-one, self-hosted digital library
solution" — Calibre's feature depth without Calibre's heavy VNC-based
container UI. Runs as a Docker container watching an "ingest" folder on
disk. Headline features per its README: automatic ingest + format
conversion, automatic metadata/cover enforcement, batch editing, automated
backups, an EPUB fixer, smart duplicate detection, "Magic Shelves,"
auto-send-to-ereader, automatic metadata fetch on ingest (multiple
providers, including an automatic **Hardcover ID fetch**), KOReader sync
(KOSync protocol), OPDS feed for e-reader apps, deep stats/analytics, and
OAuth 2.0/OIDC auth.

Confirmed by a shallow clone this session (re-verify, don't trust this list
as exhaustive or still-current): the codebase lives under `cps/`, notably
`gdrive.py` + `gdriveutils.py` (calibre-web's own **built-in Google Drive
integration**, inherited from upstream — worth understanding exactly what
role Drive plays there, since it's clearly not the same "Drive *is* the
library" model BookBrain uses), `kobo.py` / `kobo_sync_status.py` /
`kobo_auth.py` (Kobo device sync), `duplicates.py` / `duplicate_index.py`,
`metadata_provider/` (a metadata-provider abstraction, comparable to
BookBrain's `backend/app/providers/metadata/`), `magic_shelf.py`,
`opds.py`, and `services/` + `tasks/` (its background-job machinery).

## License note

CWA (and upstream calibre-web) is **GPL-3.0-or-later**. Taking *ideas* or
*architecture* and reimplementing them independently in BookBrain's own
code is unrestricted. Copying source *directly* is not free of
consequence — if a finding recommends literal code reuse, flag it and
default to the same isolation pattern already used for OpenBooks and
planned for Piper (`prompts/40`): a separate process talked to over
HTTP/CLI, never `import`ed into BookBrain's own package. Don't decide this
silently — call it out per finding and let James weigh in.

## Ground rules

- **Read-only against BookBrain.** No edits to BookBrain's own code,
  migrations, commits, or pushes. Cloning and reading CWA itself is fine
  and expected (a scratch directory, not inside the BookBrain repo).
- **Don't run CWA against anything real.** If you spin it up at all (e.g.
  in Docker, to see a feature in action rather than just reading code), use
  throwaway/sample data — never point it at James's real library, Drive
  account, or credentials.
- Read `README.md`, `SPEC.md`, `ROADMAP.md`, and `prompts/README.md`'s
  shared-context footer first, so the comparison is grounded in what
  BookBrain actually is (a Drive-hosted EPUB organizer + family PWA
  viewer — not a calibre-web-shaped self-hosted server with per-user
  accounts) rather than what CWA assumes a "digital library solution"
  looks like.

## What to actually do

1. **Read the whole README first**, including the full feature list (each
   feature has its own expandable section) and the "Features Currently
   Under Active Development" roadmap section — CWA's own open items might
   independently validate or contradict ideas already in BookBrain's
   ROADMAP.
2. **For each CWA feature area below, read the actual code** (not just the
   README's marketing description) and assess: what does it really do,
   how well does it fit BookBrain's very different architecture
   (Drive-as-source-of-truth + a stateless static viewer, vs. a local-disk
   library + calibre CLI tools + its own server-rendered multi-user web
   UI), and is there a concrete BookBrain gap it would close?
   - **Kobo/e-reader sync** (`kobo.py`, `kobo_sync_status.py`,
     `kobo_auth.py`) — directly relevant to ROADMAP's open "Kobo
     reading-stats round-trip" item. Does CWA's approach suggest a cleaner
     protocol/implementation than a raw `KoboReader.sqlite` pull?
   - **Its Google Drive integration** (`gdrive.py`, `gdriveutils.py`) —
     understand its actual role (remote backup target? library storage
     backend? something else?) before concluding whether it's relevant
     inspiration or a red herring given how differently BookBrain already
     uses Drive.
   - **Duplicate detection** (`duplicates.py`, `duplicate_index.py`) — how
     does it compare to BookBrain's Library Audit clustering/dedup
     (`backend/app/services/`)? Same false-positive problems ROADMAP
     already names for BookBrain's own threshold tuning, or a genuinely
     different/better approach?
   - **Metadata providers** (`metadata_provider/`) and the **automatic
     Hardcover ID fetch** feature — BookBrain already has deep Hardcover
     integration (`prompts/25`, `26`, `28`, `31`); is there an edge case
     or provider CWA covers that BookBrain's own
     `backend/app/providers/metadata/` doesn't?
   - **EPUB Fixer** — malformed/non-conformant EPUB repair. Does BookBrain
     have any equivalent today? If not, is this a real gap (how often do
     "genuinely broken" EPUBs actually show up in James's library) or a
     solution to a problem BookBrain doesn't have?
   - **OPDS feed** (`opds.py`) — a standard protocol lots of e-reader apps
     (including Kobo/KOReader) can browse directly. BookBrain's family
     viewer is a bespoke PWA instead — is an OPDS feed a complementary
     "also let real e-reader apps browse the library" option worth having
     alongside it, given BookBrain's Drive-hosted files?
   - **Magic Shelves, batch editing, deep stats/analytics, auto-send-to-
     ereader, automated backups** — skim each; only write up the ones
     that map onto a real BookBrain gap (compare against ROADMAP's
     "Library-health panel" and the already-shipped backup service,
     `prompts/18`, before assuming either is missing).
   - Its background job architecture (`services/`, `tasks/`) — worth a
     quick architectural look only if something there seems like a better
     answer to a problem BookBrain's own `app/jobs/` has actually hit
     (check `ROADMAP.md`'s "Done" section and `prompts/09`,
     `prompts/34` for BookBrain's own job/concurrency history before
     assuming CWA's answer is better).
3. **Don't manufacture relevance.** CWA is built for a materially different
   use case (self-hosted multi-user library server with local storage) —
   it's fine, and expected, for most of it to not apply. A short "not
   relevant, here's why" is a legitimate and useful finding, not a failure
   to find something.

## Output

1. A written assessment, saved as `CWA-REVIEW-<YYYY-MM-DD>.md` at the repo
   root (same convention as `REVIEW-<date>.md`): a short summary of what
   CWA is, then one section per feature area investigated —
   what it does, whether/how it's relevant to BookBrain, and (for anything
   worth pursuing) a concrete recommendation including the license
   consideration above.
2. For anything genuinely worth building, a **new numbered prompt file**
   in `prompts/` (continuing the existing sequence — check
   `prompts/README.md` for the next number, currently past `40`), in the
   house style (`## Why / ## Goal / ## Where it goes / ## Acceptance
   criteria / ## Gotchas`) — runnable cold in its own fresh session. Don't
   write a prompt for a "maybe someday" idea; those go back into
   `ROADMAP.md`'s "Later / maybe" section instead.
3. Update `prompts/README.md`'s index with whatever you added.
4. End with a short recommended priority order for anything you proposed,
   and an explicit list of what you looked at and decided *not* to
   pursue, with a one-line reason each — so this doesn't get
   re-investigated from scratch next time someone asks "did we ever look
   at CWA?"

## Definition of done

- CWA's actual source was read for every feature area listed above (not
  just the README's description of it) — cite specific files.
- Every recommendation states plainly whether it means "reimplement the
  idea independently" or "reuse CWA code," and flags the GPL-3.0
  consequence of the latter.
- No BookBrain code, migration, commit, or push happened.
- Nothing was run against James's real library, Drive account, or
  credentials.
