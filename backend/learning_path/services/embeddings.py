"""The sentence encoder the learning path reads meaning with.

The same pinned model grouping uses (lessons/services/semantic_grouping.py),
loaded here so the two pipelines can change independently. CPU only, offline
once downloaded. Vectors are normalised, so a dot product is the cosine.
"""

import hashlib
import json
import logging
import os
import sqlite3
from contextlib import closing
from functools import lru_cache
from pathlib import Path

import numpy as np
from django.conf import settings

MODEL = "sentence-transformers/all-MiniLM-L6-v2"
REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
DIMENSIONS = 384
_QUERY_CHUNK = 500

logger = logging.getLogger(__name__)


class EncoderUnavailable(RuntimeError):
    """The encoder could not be loaded; the learning path falls back to pending links."""


@lru_cache(maxsize=1)
def load_encoder():
    """Load the pinned model from disk, downloading it only when allowed."""
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as exc:  # noqa: BLE001 -- a broken torch install raises OSError, not ImportError
        raise EncoderUnavailable("sentence-transformers is not installed") from exc
    allow_download = os.getenv("LEARNING_PATH_ALLOW_MODEL_DOWNLOAD", "True").lower() in {"1", "true", "yes"}
    failure = None
    for local_only in (True, False):
        if not local_only and not allow_download:
            break
        try:
            return SentenceTransformer(MODEL, revision=REVISION, device="cpu", local_files_only=local_only)
        except Exception as exc:  # noqa: BLE001 -- any load failure means "unavailable"
            failure = exc
    raise EncoderUnavailable(f"Could not load {MODEL}") from failure


def _cache_path():
    return Path(settings.BASE_DIR) / "semantic_cache" / "learning_path.sqlite3"


def _key(sentence):
    return hashlib.sha256(f"{MODEL}@{REVISION}:{sentence}".encode("utf-8")).hexdigest()


def _read_cache(path, keys):
    path.parent.mkdir(parents=True, exist_ok=True)
    found = {}
    unique = list(dict.fromkeys(keys))
    with closing(sqlite3.connect(path, timeout=10)) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        for start in range(0, len(unique), _QUERY_CHUNK):
            chunk = unique[start:start + _QUERY_CHUNK]
            placeholders = ",".join("?" for _ in chunk)
            rows = connection.execute(f"SELECT key, value FROM vectors WHERE key IN ({placeholders})", chunk)
            found.update((key, json.loads(value)) for key, value in rows)
    return found


def _write_cache(path, vectors):
    with closing(sqlite3.connect(path, timeout=10)) as connection:
        connection.executemany(
            "INSERT OR REPLACE INTO vectors VALUES (?, ?)",
            [(key, json.dumps(vector)) for key, vector in vectors.items()],
        )
        connection.commit()


def embed(sentences, encoder=None, cache_path=None):
    """One normalised vector per sentence, read from the cache where possible.

    A cache that cannot be opened (locked, read-only disk) only costs speed: the
    sentences are encoded again rather than failing a publish.
    """
    sentences = list(sentences)
    if not sentences:
        return np.zeros((0, DIMENSIONS), dtype="float32")
    path = Path(cache_path) if cache_path else _cache_path()
    keys = [_key(sentence) for sentence in sentences]
    try:
        found = _read_cache(path, keys)
    except (sqlite3.Error, OSError) as exc:
        logger.warning("[Learning path] vector cache at %s is unusable, recomputing: %s", path, exc)
        path, found = None, {}
    missing = [(key, sentence) for key, sentence in dict(zip(keys, sentences)).items() if key not in found]
    if missing:
        encoder = encoder or load_encoder()
        vectors = encoder.encode(
            [sentence for _, sentence in missing],
            batch_size=32, normalize_embeddings=True, show_progress_bar=False,
        )
        fresh = {key: [float(value) for value in vector] for (key, _), vector in zip(missing, vectors)}
        if path is not None:
            try:
                _write_cache(path, fresh)
            except (sqlite3.Error, OSError) as exc:
                logger.warning("[Learning path] could not save vectors to the cache at %s: %s", path, exc)
        found.update(fresh)
    return np.array([found[key] for key in keys], dtype="float32")
