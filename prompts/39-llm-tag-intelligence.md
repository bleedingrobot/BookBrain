# Task 39 — Using the LLM-tagged data: research notes

**Status: research only, captured 2026-09-13. No approach has been chosen or
built yet.** This is the output of a "go find best practice and prior art"
research pass, done at James's request once `prompts/38-llm-tagging.md`'s
full-text pass started producing genres/moods/themes/representation/content
warnings/descriptions for real books. Read this before picking one of the
options below to actually spec and build — check in with James on direction
first, since nothing here has been prioritized against the others yet.

**Read first:** `prompts/38-llm-tagging.md` (what data actually exists:
`Book.llm_tags_json.full` once `status == "done"`, surfaced to the viewer via
`bookbrain-index.json`'s per-book `llmTags`), `prompts/29-semantic-search.md`
(the existing embedding pipeline this research assumes you'll extend rather
than replace).

## The constraint that shapes everything below

BookBrain's library-viewer is a **static PWA with no backend of its own** —
it reads Drive sidecars (`bookbrain-index.json`, `bookbrain-embeddings.bin`)
and does everything else (search, filtering) client-side in the browser. The
backend (FastAPI + SQLite) can precompute anything expensive and ship it as
a new sidecar, same pattern as `hardcover_recs_service` or the dashboard.
The library is also small — ~2,840 books — which matters a lot: research
confirms brute-force cosine similarity over a few thousand vectors runs in
single-digit milliseconds in a plain JS loop. **No vector database, ANN
index, or graph database is justified at this scale** — that's a real
finding from the research below, not a guess, and it validates that the
viewer's existing "just loop over it" semantic search is already the right
call, not something to "upgrade" infrastructure under.

## Options surveyed, roughly cheapest/most-obvious first

### 1. Tag-based "similar books" (content-based filtering)
Multi-hot vector per book over genres+moods+themes+representation+content
warnings; cosine or Jaccard similarity between books; keep top-N per book.
This is the standard technique behind most open-source book recommenders
(Jaccard-based item similarity is a common baseline; "NovelNudge" does the
SentenceTransformers-embedding version of the same idea). BookBrain already
has the exact server-side shape for this in `hardcover_recs_service.py`'s
"readers also liked" — precomputed once, shipped as a sidecar. This would
be the same pattern with a locally-derived signal instead of Hardcover's
crowd data, and the two could be blended (hybrid content+collaborative is
standard RecSys practice).

**Explainability matters here and is nearly free**: research (the
"Tagsplanations" paper, general XAI-UI guidance) consistently finds that
showing *which* tags two books share ("because you both have: found family,
morally grey protagonist") is what makes a tag-based rec trustworthy versus
a bare similarity score. BookBrain's tags are already categorical and
human-readable — this falls out for free.

### 2. Enrich the existing embedding pipeline
`embedding_service.py` already embeds title/author/series/blurb with a
local ONNX model (all-MiniLM-L6-v2) and ships vectors as
`bookbrain-embeddings.bin` for in-browser semantic search. Feeding it the
new `shortDescription`/`longSummary`/`themes` too would improve the
*existing* search for free, and doubles as an independent "more like this"
signal (nearest-neighbor in embedding space) alongside option 1's tag-based
one. Near-zero new infrastructure — just a richer `embed_input`.

### 3. Auto-clustered "shelves"
Cluster the same tag/embedding vectors (k-means, or graph community
detection — Louvain/Leiden) to auto-discover thematic groupings ("dark
academia," "cosy found-family fantasy"), distinct from the viewer's existing
*admin-authored* smart collections (hand-written rules, not discovered).
Direct prior art: a project clustering Goodreads shelves via ML into
genre-like groups. Complements the manual collections feature rather than
replacing it.

### 4. A relationship graph ("see relationships," literally)
Precompute a graph server-side once per index rebuild — nodes = books/
authors/series, edges = shared theme/mood above a threshold, or explicit
same-series/same-author — ship it as a plain adjacency JSON sidecar, render
client-side with a force-directed layout. Close prior art: **BookGraph**
(github.com/sumant1122/bookgraph), which uses an LLM to extract typed
relationships ("Influenced By," "Contradicts," "Expands") between books and
renders them as an interactive force-graph "galaxy." Its stack (Neo4j +
FastAPI + Next.js) is heavier than BookBrain needs — borrow the
*relationship typing* and *visualization concept*, not the graph database
(a JSON adjacency list is plenty for ~2,840 nodes). Rendering library: the
practical 2026 read is **d3-force** for full control + small footprint,
**Cytoscape.js** only if you actually want graph algorithms (centrality,
pathfinding) rather than just motion, **Sigma.js** only if the graph gets
big enough to need WebGL — it won't, at this scale.

### 5. GraphRAG-style community summaries
Microsoft's GraphRAG pattern: LLM-extract entities/relationships, run
community detection (Leiden) to find densely-connected clusters, have the
LLM write one summary *per cluster* rather than per-document. Mapped onto
BookBrain: cluster by tag/theme overlap, have Ollama write a paragraph per
cluster ("this shelf is your morally-grey political fantasy"). Real,
validated technique — the heaviest option here, not a starting point.

### 6. "Ask your library" via the idle Ollama instance
Ollama now sits idle outside its tagging windows (prompts/38). A lightweight
RAG pattern — retrieve candidate books by tag/embedding match, one LLM call
to synthesize an answer citing specific books — fits the general shape of
small local-RAG projects (embeddings + Ollama synthesis), scaled down since
BookBrain's "documents" are book records, not long files needing chunking
(the tagging map-reduce already *is* that indexing step, just built for
tagging rather than Q&A). Catch: this would have to live on the backend
(like tagging itself) — the viewer has no server, and exposing Ollama's
Tailscale endpoint to a public GitHub-Pages site isn't something you'd want.

## Recommended sequencing (opinion, not yet agreed with James)

Start with **#2 then #1** — both cheap, both reuse infrastructure that
already exists, both give "more like this" and better search almost
immediately. **#3** (auto-shelves) is a good next step once #1/#2 have been
eyeballed against real data. **#4** (the graph) is the most literal answer
to "see relationships" and the most fun, but it's principally presentation
value unless it's built on top of the similarity data #1-#3 already
compute — do the data first, the graph of it is comparatively easy once it
exists. **#5** and **#6** are real techniques worth keeping in mind, not
starting points — they solve a richer problem than "what should I read
next," and neither needs a vector or graph database to eventually build.

## Update — 2026-09-13, notes from an external architecture PDF

James shared a document ("Multidimensional Semantic Retrieval, Dynamic
Clustering, and Personalized Recommendation Architectures for Enriched
Digital Libraries") to check against this plan. Worth being direct about
what it is: it reads as an AI-generated "deep research" report — dense with
formulas (ACT-R cognitive activation, Rao's Quadratic Entropy, UMAP +
HDBSCAN* + Leiden) and its works-cited list mixes legitimate arXiv papers
with Reddit threads and unrelated AI-coding-agent memory-system projects.
Its core proposal is a brand-new SQLite schema (`books`, `book_genres`,
`book_tones`, `book_content_warnings` junction tables, `books_fts` FTS5,
`books_vec` via `sqlite-vec`) — written with zero knowledge of BookBrain's
actual models (`Book`/`Author`/`Series`/`File`, JSON blobs like
`llm_tags_json`/`hardcover_json`, embeddings as raw bytes). **Not adopting
the schema or the general architecture wholesale** — it's solving for a
generic "enriched digital library," not this specific ~2,840-book
single-family app, and several of its recommendations (Postgres/pgvector as
an alternative, `sqlite-vec`, physical-shelf-location analytics, an ACT-R
personalization model) are disproportionate to BookBrain's actual scale and
already-established "stay backend-light" posture (see the "no vector
database is justified at this scale" note above, which this document
independently agrees with in its own summary table, for what that's worth).

That said, a few specific, real techniques it names are worth folding into
the options above:

- **Reciprocal Rank Fusion (RRF)** for combining lexical (keyword/title
  substring) and semantic (embedding) search results — rank-based, needs no
  score normalization, robust when score distributions aren't comparable.
  Directly applicable to option 2: BookBrain's viewer currently runs
  filename/title matching and semantic search as separate modes rather than
  fusing them, and RRF is simple enough to implement client-side in JS.
- **MMR (Maximal Marginal Relevance)** reranking for diversity — when
  building the tag/embedding-based "similar books" list in option 1, MMR
  stops the top-N from being five near-duplicates of the same sub-genre by
  penalizing candidates too similar to ones already picked. Cheap, worth
  building in from the start rather than bolting on later.
- **Leiden over Louvain** specifically, if option 4's relationship graph
  ever gets a community-detection pass — Leiden fixes a real Louvain flaw
  (it can produce disconnected "communities"), confirming/sharpening what
  was already a Louvain-or-Leiden note above.
- **c-TF-IDF for auto-naming clusters** — option 3 (auto-clustered shelves)
  had a gap: once you cluster books, what do you call the shelf? Class-based
  TF-IDF (the technique behind BERTopic's topic labels) extracts the terms
  most distinctive to a cluster versus the corpus as a whole — a concrete,
  well-known answer to that gap, worth adopting whenever option 3 gets
  built.
- **UMAP + HDBSCAN\*** as an alternative to plain k-means/Louvain for option
  3 — legitimate, doesn't require guessing a cluster count upfront, handles
  outliers as noise rather than forcing every book into a shelf. Heavier
  dependency-wise (both are real libraries with C extensions) — worth it if
  k-means turns out too coarse, not a reason to skip the simpler version
  first.
- **Hard content-warning filtering as a small standalone feature** — the
  document's "safety filter" is really just: let a reader exclude books
  carrying a specific content warning before anything else ranks. That's a
  small, concrete, near-term-buildable feature in its own right (the tags
  already exist), independent of whichever recommendation approach above
  gets built first.
- **Natural-language mood queries** ("dark but hopeful, non-human
  characters") parsed into structured filters (tones, archetypes) plus an
  embedding of the free text — a lighter-weight cousin of option 6's "ask
  your library" chat, without needing a full RAG/LLM-synthesis round trip.
  Worth keeping as a middle-ground option between plain facet filtering and
  a full chat interface.

Explicitly **not** carrying forward: the proposed SQL schema (BookBrain's
existing models are the schema to extend, not this one), `sqlite-vec` /
Postgres+pgvector adoption now (unnecessary at this scale per the research
above — worth revisiting only if the library grows by an order of
magnitude), the ACT-R-based personalization/activation-decay model (assumes
a per-user interaction-logging system BookBrain doesn't have, and is a lot
of cognitive-science machinery for "boost unread books a bit"), Rao's
Quadratic Entropy for collection-diversity analytics (a real metric, just
more esoteric than a plain genre-count histogram would already communicate),
and anything about physical shelf location (BookBrain has no physical
shelving concept — Drive-based ebooks only).

## Update — 2026-09-13 (deep dive), github.com/seaberger/libtrails

James asked for a full deep dive on this repo specifically. Worth saying up
front: unlike the PDF above, **this is real, running, documented code
solving almost exactly this problem** — a personal EPUB library, local LLM
(Ollama, `gemma3:27b`/`gemma3:4b`) topic extraction, embeddings, Leiden
clustering, a relationship graph, a "galaxy" visualization. It's the closest
prior art found so far to options 1-4 above, and its docs (`docs/topic-
extraction-pipeline.md`, `docs/graph-clustering-pipeline.md`, `docs/hybrid-
search-architecture.md`, `docs/leiden-clustering-optimization.md`, `docs/
domain-generation-methodology.md`, `docs/galaxy-visualization.md`) include
real config values, real failure post-mortems, and real citations — much
more actionable than the PDF's generic formulas. That said, it's built for
a different shape of library (927-2,000+ books, both fiction and dense
nonfiction, extracting ~100+ atomic topics *per book* rather than a
handful of controlled-vocabulary tags) and ships as its own standalone app
(FastAPI + Astro/React-Three-Fiber), not a static sidecar-fed viewer — so
calibrate what's borrowed against BookBrain's actual scale and shape, same
as with the PDF.

### Directly actionable for prompts/38's tagging pipeline itself

- **Batch multiple chunks into one Ollama call.** libtrails' Pass 2 doesn't
  call the model once per chunk — it batches 5 chunks into a single prompt
  (`--- Passage 1 ---` ... `--- Passage 5 ---`, numbered) and gets back one
  JSON object keyed by passage number, with a per-chunk fallback if the
  batch parse fails. BookBrain's map step currently spends one full Ollama
  round-trip per chunk (33-42 calls for a typical novel). Given `qwen3:14b`
  supports up to 40,960 tokens of context (confirmed via the live `/api/tags`
  check when `OLLAMA_NUM_CTX` was set up) and BookBrain's chunks are already
  sized to nearly fill `ollama_num_ctx` (8192) individually, batching would
  mean either raising `ollama_num_ctx` significantly or shrinking individual
  chunk size to fit 2-3 per call — a real, concrete lever to cut total
  tagging time further, on top of the earlier scheduler-interval fix.
- **A stoplist for generic single-word themes.** libtrails filters exactly
  30 generic single-word topics before storage (`power`, `love`, `identity`,
  `conflict`, `survival`, `freedom`, `control`, `trust`, `fear`, `growth`,
  `time`, `people`, `world`, `change`, `future`, etc.) because they pollute
  clustering — every book touches "power" or "identity" at some level, so
  they become meaningless hub connectors. BookBrain's own real output
  already shows this pattern (*Grey Sister*'s themes included bare
  "identity", "survival", "resilience" alongside genuinely specific ones
  like "healing and sacrifice"). Worth adopting a similar stoplist — or
  folding "must be a multi-word specific phrase, not a bare abstract noun"
  into the tagging prompt itself — before ever using themes as graph edges
  (option 4) or cluster input (option 3), since libtrails' own war story
  (below) shows exactly what happens if you don't.
- **Metadata tag hygiene.** `clean_calibre_tags()` strips noise values
  ("General", "Fiction", "Unknown"), normalizes compound variants ("Fiction
  - Science Fiction" → "Science Fiction"), and removes substring
  duplicates. A similar pass over BookBrain's own generated genre lists
  (dropping near-universal, uninformative values) would help before they
  feed into similarity/clustering.

### The two-tier deduplication problem BookBrain will hit

libtrails discovered that an LLM asked the same kind of question across
many books/chunks produces lots of near-duplicate phrasings of the same
idea ("spiritual journey" vs "spiritual awakening" vs "inner journey").
Their fix, a **Union-Find merge with two confidence tiers**:

| Cosine similarity | Action |
|---|---|
| > 0.95 | merge unconditionally (obvious duplicates) |
| 0.85 – 0.95 | merge only if the two topics also share at least one book (prevents unrelated-but-similar-sounding ideas from different domains merging — their example: "energy manipulation" in fantasy vs. "psychological manipulation" in psychology) |
| < 0.85 | no merge |

BookBrain will hit the same problem the moment it tries to compare themes
*across* books rather than just display them per-book — the real data
already shows it ("power and suppression" / "power and control" / "power
and corruption" as separate phrasings across different books in the
`Holy Sister`/`Grey Sister` results seen so far). This two-tier rule is a
concrete, ready-to-borrow answer whenever option 1 or option 4 gets built.

### If a real relationship graph (option 4) gets built: a concrete recipe

libtrails' graph-construction pipeline, in order, with real parameter
values:

1. **Hybrid edges, not one source**: co-occurrence edges (weight = `PMI ×
   (1 + log(1 + book_count))` — pairs sharing more books get boosted, e.g.
   a pair in 5 books gets ~2.8× the weight of a pair sharing none) *plus*
   top-10 nearest-neighbor embedding edges with a **similarity floor of
   0.65** (prevents linking things that are only "neighbors" because the
   embedding space is sparse out there). PMI itself is filtered to ≥ 1.0 —
   below that, a co-occurrence is "chance level," not a real signal.
2. **Remove hub nodes before clustering — this one is not optional.**
   libtrails' own post-mortem: running Leiden without hub removal produced
   a single mega-cluster of 10,100 topics (6.6% of everything) that got
   labeled "Personal Journeys" — because the hub topic "relationships"
   (35,458 occurrences) sat next to "conflict," which sat next to "space
   exploration" and "artificial intelligence," transitively dragging
   completely unrelated content into one bucket. The fix: remove the top
   ~5% of topics by degree *before* clustering, cluster the rest, then
   reassign the removed hubs to whichever resulting cluster they neighbor
   most. This is the single most important lesson in the whole repo if
   BookBrain ever builds a theme/mood co-occurrence graph — a tag like
   "Fantasy" or "adventure," present on a large fraction of the library,
   would do exactly the same damage without this step.
3. **Leiden with CPM (Constant Potts Model), not modularity** — CPM gives
   more even-sized, tunable-resolution clusters; modularity alone produced
   only 33 very broad categories in their comparison. Resolution is the
   knob: they landed on 0.001-0.002 for ~300-1,300 clusters depending on
   hub removal, out of 100K+ topics — BookBrain's own theme/genre
   vocabulary is far smaller (probably low hundreds of distinct values
   across ~2,840 books, not 100K+), so this exact number doesn't transfer,
   but "CPM, tune resolution empirically, expect to iterate" does.
4. **Two-level hierarchy**: K-means on Leiden cluster centroids produces a
   smaller number of broad "domains" (super-clusters) — their **robust
   centroid** recipe: drop topic labels under 4 characters, take the top 15
   topics by occurrence count, weight by `log1p(occurrence_count)` for
   stability. Each domain then gets an LLM-drafted label plus human
   refinement. This directly extends option 3's "auto-shelves" gap (what do
   you call a cluster?) with a genuine two-level structure: broad domain →
   specific cluster → books, which maps cleanly onto a tabbed/two-panel
   viewer UI (their own "Themes browser" does exactly this).
5. **Visualization parameters, if a galaxy/graph view is ever wanted**:
   UMAP with `n_neighbors=15, min_dist=0.3, metric='cosine'`, projecting
   cluster/domain centroids (not individual books) to 2D or 3D; colors
   assigned by a second PCA pass over domain centroids mapped to a hue
   range (avoiding red's wraparound at both ends of the circle) so that
   semantically similar domains land on similar colors rather than
   arbitrary ones.

### On search fusion (extends option 2's "add RRF" note)

libtrails names the actual algorithm and paper: **Reciprocal Rank Fusion**
(Cormack, Clarke & Buettcher, SIGIR 2009), `score += 1/(k + rank + 1)` per
signal with `k=60` as the standard default — no score normalization needed
at all, which is why it beats trying to reconcile BM25 and cosine scores on
different numeric scales (the PDF's more complicated "Z-score Convex
Combination" approach was solving a problem RRF sidesteps entirely). Their
book search fuses **7 separate signals** (title/author/description keyword
match, semantic search over individual extracted topics, semantic search
over book-level theme labels, a purpose-built whole-book embedding, and
chunk-content keyword + semantic search) and degrades gracefully — falling
back to fewer signals if some indexes aren't built yet. BookBrain doesn't
need 7 signals to start, but the pattern (keyword match + semantic
similarity, fused by rank rather than score, each signal independently
optional) is exactly the shape option 2 should take.

One more useful, citation-backed confirmation: libtrails explicitly avoids
building a book-level vector by averaging its chunk embeddings ("a 400-page
novel about war would average out to a generic 'fiction' vector because
most chunks are dialogue and scene-setting, not core themes" — citing
Günther et al.'s "Late Chunking," 2024, and Arora et al., ICLR 2017).
Instead they build one **purpose-built** vector per book from title +
description + themes. That's exactly the shape of this plan's option 2
(enrich `embed_input` with the new short/long descriptions and themes,
rather than trying to derive a book vector from the tagging chunks) — good
independent validation that the cheap option was also the right one.

### The scale lesson — and why it doesn't apply to BookBrain yet

libtrails hit a real wall: with **108,668 raw extracted topics** (~117 per
book before dedup, across ~927 books), a naive full pairwise similarity
matrix (`embeddings @ embeddings.T`) needed ~47GB of RAM and 80+ minute
runs that never finished. Their fix was k-NN via scikit-learn instead of
computing every pair. This is the concrete version of the "don't reach for
a vector database at BookBrain's scale" conclusion from the PDF review
above — except it also shows *where the line actually is*: the danger
appears once item count gets into the tens/hundreds of thousands, which
happens fast if you extract many small atomic topics per chunk (libtrails'
approach) rather than a handful of controlled tags per book (BookBrain's
current approach). BookBrain's book-level tags/genres/themes will likely
stay in the low hundreds of distinct values across ~2,840 books — nowhere
near this cliff — which is itself a reason to prefer the simpler per-book
tag approach (options 1-3) over adopting libtrails' atomic-topic-per-chunk
architecture wholesale: it sidesteps a scaling problem BookBrain doesn't
need to solve. If BookBrain ever *does* want per-chunk atomic topics (a
genuinely richer graph, closer to what libtrails builds), budget for this
problem from day one rather than discovering it at 80 minutes into a run.

### Worth knowing: even libtrails hasn't closed this loop

Its own roadmap still lists **"book recommendations based on topic
overlap"** as unimplemented (Future, not Completed) — the actual
recommendation-generation step (option 1 in this plan) is still open even
in the most mature prior art found. Nothing here is "already solved,
nothing left to build."

## Sources

- [book-recommender · GitHub Topics](https://github.com/topics/book-recommender)
- [artisan1218/Recommendation-System (Jaccard-based item similarity)](https://github.com/artisan1218/Recommendation-System)
- [Tagsplanations: Explaining Recommendations Using Tags](https://files.grouplens.org/papers/vig-iui2009-tagsplanations.pdf)
- [sumant1122/bookgraph](https://github.com/sumant1122/bookgraph)
- [totogo/awesome-knowledge-graph](https://github.com/totogo/awesome-knowledge-graph)
- [Client-side semantic search for your static site](https://bart.degoe.de/semantic-search-in-your-browser/)
- [SemanticFinder — frontend-only semantic search with transformers.js](https://geo.rocks/post/semanticfinder-semantic-search-frontend-only/)
- [client-vector-search](https://github.com/yusufhilmi/client-vector-search)
- [sqlite-vec](https://github.com/asg017/sqlite-vec)
- [From Local to Global: A Graph RAG Approach to Query-Focused Summarization](https://arxiv.org/pdf/2404.16130)
- [GraphRAG — Community detection](https://www.mintlify.com/microsoft/graphrag/concepts/community-detection)
- [Exploring RAG and GraphRAG (Weaviate)](https://weaviate.io/blog/graph-rag)
- [Cytoscape.js vs vis-network vs Sigma.js 2026](https://www.pkgpulse.com/blog/cytoscape-vs-vis-network-vs-sigma-graph-visualization-javascript-2026)
- [ahegel/genre-ML-clustering](https://github.com/ahegel/genre-ML-clustering)
- [amscotti/local-LLM-with-RAG](https://github.com/amscotti/local-LLM-with-RAG)
- [seaberger/libtrails](https://github.com/seaberger/libtrails) — the full deep dive: README, `docs/topic-extraction-pipeline.md`, `docs/graph-clustering-pipeline.md`, `docs/hybrid-search-architecture.md`, `docs/leiden-clustering-optimization.md`, `docs/domain-generation-methodology.md`, `docs/galaxy-visualization.md`
- Cormack, Clarke & Buettcher (2009), *Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods*, SIGIR '09 (cited by libtrails for RRF)
- Günther et al. (2024), *Late Chunking: Contextual Chunk Embeddings Using Long-Context Embedding Models* (cited by libtrails for why not to average chunk embeddings)
- Arora et al. (2017), *A Simple but Tough-to-Beat Baseline for Sentence Embeddings*, ICLR 2017 (same)
