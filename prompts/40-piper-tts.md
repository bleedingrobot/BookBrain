# Task 40 — Replace read-aloud with Piper (local neural TTS)

**Not started.** James: replace the current read-aloud voice outright — this
is not an opt-in alongside the browser voice, Piper becomes the only TTS
engine once it ships.

## What it is

`library-viewer/src/lib/tts.ts` currently drives the browser's built-in Web
Speech API (`speechSynthesis` / `SpeechSynthesisUtterance`) for the reader's
"🔊 Listen" panel (`components/Reader.tsx`). It's robotic and voice
availability is inconsistent across browsers/devices — not worth keeping as
the long-term voice. ROADMAP.md (§"Replace read-aloud's voice with Piper")
already has a spike behind it (2026-09-13):
[Piper](https://github.com/OHF-Voice/piper1-gpl) (GPL-3.0, the neural TTS
Home Assistant uses) installed in a scratch venv, `en_US-lessac-medium`
voice (63 MB), a sample paragraph synthesized and judged "clearly more
natural" than the browser voice by James. CPU-only on this server: 17.3s of
audio in 2.9s wall time (~6x real-time incl. model load) — comfortably fast
enough to stream as you read, *if* the synthesis call can reach the reader
at all (see the open decision below).

None of `lib/tts.ts`'s existing machinery needs to change conceptually: it
already walks the current EPUB section block-by-block via foliate's
`view.initTTS()`, flattens each block's SSML to plain text
(`ssmlToText()`), and drives page-turns/highlighting off `tts.setMark('0')`.
Only the "speak this text" leaf — currently
`new SpeechSynthesisUtterance(text)` — needs to become "get audio for this
text from Piper and play it."

## Decision needed before writing any code

**This determines the whole architecture below — resolve it with James
first.**

The family library-viewer (`library-viewer/`) is a **static site** deployed
to GitHub Pages with no server of its own. Every other feature that needs
something the backend computed reaches the viewer through the same
established pattern: the backend computes it, uploads a JSON sidecar to the
user's Drive, the viewer reads the sidecar from Drive's API — never a live
call to BookBrain's own backend (which only runs on James's home server).
`prompts/39` names this constraint explicitly for a similar case: "the
viewer has no server, and exposing [a home-server endpoint] to a public
GitHub-Pages site isn't something you'd want."

ROADMAP.md's spike sketch assumed a **live per-block HTTP call** from the
viewer straight to a new backend endpoint — that only works for a reader
whose device can actually reach the home server (same LAN, or over
Tailscale) at the moment they hit play. Whether that's an acceptable
constraint depends on how the family actually uses the viewer, which is
**deliberately left open** — ask James before picking a path:

- **A — Live per-block call.** Closest to the ROADMAP sketch and the
  smaller build. TTS only works for a reader who can currently reach the
  home server; unreachable devices lose read-aloud entirely (no fallback,
  since the browser-voice fallback is being removed by this task).
- **B — Pre-synthesized audio, shipped as a Drive sidecar.** Matches the
  established backend-generates → Drive-carries → viewer-reads pattern
  every other viewer feature uses (news, reading status, embeddings,
  new-releases). Works identically at home or away. Materially bigger
  scope: a new pipeline step (nightly, or an on-demand admin trigger) that
  synthesizes audio per book, storage cost (compressed audio is still much
  larger than the sidecar JSONs this codebase is used to), and a
  regeneration story if a book's text changes. Chunking granularity matters
  here — per EPUB section (dozens of files/book, matches foliate's own
  section-at-a-time block iteration) is a saner unit than per-paragraph
  (could be thousands of tiny files) or one file per whole book (no
  reasonable seek/resume without separate timing metadata).

Find out which before scoping the rest of this prompt down to one path —
don't build A and then discover James actually needed B (or vice versa).

## Recon first, same lesson as every other integration this project has hit

Don't assume the 2026-09-13 spike's exact numbers/interface still hold —
confirm live, the way the Libgen provider work this session had to (Anna's
Archive burned real time on stale assumed domains; Libgen itself needed a
live check of which mirrors actually still work and whether a User-Agent
was required before anything else got built). Before designing the actual
integration:

- Confirm `piper1-gpl`'s current installed interface — is it a Python
  library you call in-process (`PiperVoice.load(...)`, `.synthesize(text)`),
  a CLI with a `--http`-style server mode, or does it need a thin wrapper
  script to expose HTTP at all? The ROADMAP spike used "a scratch venv" —
  reread its exact steps/output if still available, then re-verify against
  whatever version installs today rather than trusting the prior numbers.
- Re-time synthesis latency on **this actual server** (the one
  `bookbrain.service` runs on) — the spike's 2.9s/17.3s-of-audio number is
  real but from 2026-09-13; re-confirm it's still representative before
  designing around it, especially for path B where a whole book's worth of
  synthesis work matters for pipeline runtime.
- Re-listen to `en_US-lessac-medium` (or try alternatives) before locking
  in a voice — the spike only tried the one.

## Platform note

Older prompts (e.g. `prompts/37`'s `openbooks_process_service.py`) assume a
**Windows** dev/deploy target (`.exe` binaries, PowerShell scripts,
`taskkill`/`netstat` fallbacks) — that's stale. This deployment now runs on
a **Linux home server** via systemd (`bookbrain.service`, plus sibling units
like the dashboard services — `systemctl list-units | grep bookbrain` shows
the current set). If Piper needs to run as a standalone process managed
independently of the FastAPI backend (the GPL-isolation reasoning below
argues for this), a dedicated systemd unit fits this deployment's existing
idiom better than a backend-managed Windows-style subprocess-with-UI-button
— see how `backend/tools/run-flaresolverr.sh` was scaffolded as "one script
+ one env var" for a comparable optional sidecar process.

## Why a separate process at all

GPL-3.0 isn't a concern for BookBrain's own (differently licensed) code as
long as Piper stays a **separate process** reached over HTTP or similar —
not `import`ed directly into `backend/app/`. Same reasoning already applied
to OpenBooks. Keep that boundary: a small standalone script/service that
depends on `piper1-gpl` directly, talked to only over the network, never a
dependency of the main backend package.

## Shape (sketch — firm up once path A vs B is picked)

### Backend

- A new Piper-serving process (systemd unit, per the platform note above) —
  whatever thin wrapper Piper's actual current interface (per Recon above)
  turns out to need, exposing "synthesize this text in this voice" over
  HTTP.
- **Path A**: a `piper_tts_service.py` HTTP client (mirrors
  `openbooks_service.py`'s role) + a route the viewer calls per block,
  streaming back audio.
- **Path B**: a new pipeline step (service module akin to `cover_service.py`
  or the nightly steps in `app/jobs/nightly.py`) that walks a book's
  sections, synthesizes each, uploads the resulting audio (+ per-block
  timing/marks metadata, so `tts.setMark('0')`-style sync still works
  against pre-rendered audio) into a Drive sidecar structure, plus a
  regeneration trigger (nightly and/or an admin "Generate audio" button
  akin to the Discovery-data refresh panel from `prompts/35`).
- Config: an enabled flag + voice name, following the
  `annas_archive_enabled`/`libgen_enabled` pattern in `app/core/config.py`
  — off by default until it's actually ready, same as every other
  experimental integration in this codebase.

### Frontend (`library-viewer`)

- `lib/tts.ts`: swap the `SpeechSynthesisUtterance`-based "speak this text"
  leaf for a fetch-and-play (Path A) or a "load this block's pre-rendered
  audio" (Path B) leaf. Keep the SSML→text extraction, block iteration, and
  `tts.setMark('0')` page-turn/highlight sync exactly as they are — that
  machinery isn't voice-engine-specific.
- Reader.tsx's read-aloud panel: drop the browser voice `<select>` (no
  longer meaningful with one server-side voice) — decide whether a
  server-side voice picker replaces it, or whether voice becomes a fixed
  choice for now.
- `readerPrefs.ts`'s `ttsVoiceURI` either gets repointed at a Piper voice
  id or removed if voice choice isn't user-facing yet.

### Tests

- Backend: unit tests for the new service module(s), respx/mocked HTTP
  against the Piper process (same convention as
  `tests/test_openbooks_service.py`'s fake local server, or
  `tests/test_annas_archive_provider.py`'s respx-mocked HTTP pattern).
- Frontend: `lib/tts.test.ts` already covers the DOMParser-fallback path —
  extend it for whichever leaf replaces `SpeechSynthesisUtterance`, without
  needing a real Piper process running in CI.

## Gotchas / notes

- The current TTS feature has **zero server load or cost** — it's 100%
  client-side. Either path here moves real compute (and, for path B,
  storage) onto James's own server. Worth keeping visible as a real
  tradeoff, not a free upgrade.
- Piper voices are per-language/accent — check what's available before
  assuming `en_US-lessac-medium` is the final answer; this only matters if
  the household ever reads books in another language.
- If path B is chosen: think about what happens to in-flight/queued reading
  the moment a book is re-organized, re-identified, or its text otherwise
  changes — stale pre-rendered audio should get regenerated, not silently
  serve outdated narration forever.

## Acceptance criteria

- Path A or B decided with James and written back into this file before
  the rest of the shape is finalized.
- Read-aloud in the viewer plays Piper audio for a book, with the same
  play/pause/skip/page-turn/highlight behavior the browser-voice version
  has today.
- The Web Speech API path is fully removed once Piper is verified working
  (or gated in a way James is happy shipping, if a fallback for
  unreachable-server cases is ultimately wanted for path A) — no dead code
  left behind either way.
- Full test suite green in whichever of `backend/`/`library-viewer/` was
  touched; browser-verified against a real EPUB, not just unit-tested.
