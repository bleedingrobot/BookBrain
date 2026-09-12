"""prompts/29 Phase 1 — a local sentence embedding per organised book, so the
static library-viewer can do natural-language ("meaning") search: it downloads
a `bookbrain-embeddings.bin` sidecar, loads the *same* tiny model once, and at
query time embeds only the query and ranks by cosine similarity.

Model: `all-MiniLM-L6-v2` (384-dim), the quantized ONNX from
`Xenova/all-MiniLM-L6-v2` — the exact file the viewer vendors, so the vectors
match on both sides. Run with onnxruntime + tokenizers (no torch). Mean-pool
the token embeddings with the attention mask, then L2-normalise — the standard
sentence-transformers recipe, and what transformers.js does with
`{pooling: 'mean', normalize: true}`.

Incremental: each book stores a hash of its embed input; a run only re-embeds
what changed. Runs in the nightly job + `POST /api/library/embeddings/refresh`.
No network calls beyond the one-time model download, no AI cost.
"""

import hashlib
import logging

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy.orm import selectinload

from app.data.models import Book, File, FileStatus, MetadataSource
from app.services.library_index_service import _plain_text

logger = logging.getLogger(__name__)

MODEL_ID = "all-MiniLM-L6-v2"
DIM = 384
_HF_REPO = "Xenova/all-MiniLM-L6-v2"
_ONNX_FILE = "onnx/model_quantized.onnx"
_MAX_TOKENS = 256
_BATCH = 64

_session = None  # lazy onnxruntime.InferenceSession
_tokenizer = None


def _load() -> tuple:
    """Fetch the model + tokenizer from the HF Hub (cached after the first
    call) and build the onnxruntime session. Lazy so importing this module —
    and every test that doesn't embed — stays cheap."""
    global _session, _tokenizer
    if _session is None:
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        model_path = hf_hub_download(_HF_REPO, _ONNX_FILE)
        tok_path = hf_hub_download(_HF_REPO, "tokenizer.json")
        _session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        tok = Tokenizer.from_file(tok_path)
        tok.enable_truncation(max_length=_MAX_TOKENS)
        tok.enable_padding()
        _tokenizer = tok
    return _session, _tokenizer


def embed_texts(texts: list[str]) -> np.ndarray:
    """(N, 384) float32, L2-normalised. Empty input → (0, 384)."""
    if not texts:
        return np.zeros((0, DIM), dtype=np.float32)
    session, tokenizer = _load()
    out = np.empty((len(texts), DIM), dtype=np.float32)
    for start in range(0, len(texts), _BATCH):
        chunk = texts[start : start + _BATCH]
        encs = tokenizer.encode_batch(chunk)
        ids = np.array([e.ids for e in encs], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
        types = np.array([e.type_ids for e in encs], dtype=np.int64)
        (hidden,) = session.run(
            ["last_hidden_state"],
            {"input_ids": ids, "attention_mask": mask, "token_type_ids": types},
        )
        m = mask.astype(np.float32)[:, :, None]
        pooled = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        out[start : start + len(chunk)] = pooled / np.clip(norms, 1e-9, None)
    return out


def embed_input(title: str, author: str | None, series: str | None, blurb: str | None) -> str:
    """What we actually embed — title/author/series always present so a book
    with no blurb still ranks on its own name; mirrors the index's own
    `book.description or epub_description` fallback (passed in as `blurb`)."""
    parts = [title.strip()]
    if author:
        parts.append(author.strip())
    if series:
        parts.append(series.strip())
    text = _plain_text(blurb)
    if text:
        parts.append(text)
    return ". ".join(parts)


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()  # noqa: S324 — not security


async def refresh_embeddings(
    session: AsyncSession,
    *,
    limit: int = 500,
    embed_fn=embed_texts,
) -> dict:
    """Re-embed organised books whose embed input changed (or was never
    embedded), capped at `limit`. `embed_fn` is injectable so tests don't
    download the model."""
    files = (
        (
            await session.execute(
                select(File, Book)
                .join(Book, Book.id == File.book_id)
                .where(File.status == FileStatus.organised, File.book_id.is_not(None))
                .options(selectinload(Book.author), selectinload(Book.series))
            )
        )
        .all()
    )

    file_ids = [f.id for f, _ in files]
    epub_desc: dict[int, str] = {}
    if file_ids:
        for fid, value in (
            await session.execute(
                select(MetadataSource.file_id, MetadataSource.value).where(
                    MetadataSource.file_id.in_(file_ids),
                    MetadataSource.field_name == "description",
                )
            )
        ).all():
            epub_desc.setdefault(fid, value)

    pending: list[tuple[Book, str, str]] = []
    unchanged = 0
    for f, book in files:
        text = embed_input(
            book.canonical_title,
            book.author.name if book.author else None,
            book.series.name if book.series else None,
            book.description or epub_desc.get(f.id),
        )
        h = _hash(text)
        if book.embedding is not None and book.embedding_hash == h and book.embedding_model == MODEL_ID:
            unchanged += 1
            continue
        pending.append((book, text, h))

    to_do = pending[:limit]
    embedded = 0
    for start in range(0, len(to_do), _BATCH):
        chunk = to_do[start : start + _BATCH]
        vectors = embed_fn([t for _, t, _ in chunk])
        for (book, _text, h), vec in zip(chunk, vectors, strict=True):
            book.embedding = np.asarray(vec, dtype=np.float32).tobytes()
            book.embedding_hash = h
            book.embedding_model = MODEL_ID
            embedded += 1
        await session.commit()

    logger.info(
        "embeddings: %d embedded, %d unchanged, %d still pending",
        embedded,
        unchanged,
        len(pending) - embedded,
    )
    return {"embedded": embedded, "unchanged": unchanged, "pending": len(pending) - embedded}
