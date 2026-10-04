"""The learning path's own encoder loader and vector cache (spec section 4)."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
from django.test import SimpleTestCase

from .services import embeddings


class FakeEncoder:
    def __init__(self):
        self.calls = []

    def encode(self, sentences, **options):
        self.calls.append(list(sentences))
        return np.array([[1.0, 0.0] if "solid" in sentence else [0.0, 1.0] for sentence in sentences])


class EmbedTests(SimpleTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.cache = Path(folder.name) / "vectors.sqlite3"

    def test_one_row_per_sentence_in_order(self):
        vectors = embeddings.embed(["a solid", "a gas", "a solid"], encoder=FakeEncoder(), cache_path=self.cache)

        self.assertEqual(vectors.tolist(), [[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]])

    def test_a_second_call_reads_the_cache(self):
        encoder = FakeEncoder()
        embeddings.embed(["a solid"], encoder=encoder, cache_path=self.cache)
        embeddings.embed(["a solid"], encoder=encoder, cache_path=self.cache)

        self.assertEqual(encoder.calls, [["a solid"]])

    def test_an_unusable_cache_still_gives_vectors(self):
        """Review finding: a locked or unwritable cache must not fail a publish."""
        with self.assertLogs("learning_path.services.embeddings", level="WARNING"):
            vectors = embeddings.embed(["a solid"], encoder=FakeEncoder(), cache_path=self.cache.parent)

        self.assertEqual(vectors.tolist(), [[1.0, 0.0]])

    def test_no_sentences_give_an_empty_matrix(self):
        self.assertEqual(embeddings.embed([], encoder=FakeEncoder(), cache_path=self.cache).shape, (0, embeddings.DIMENSIONS))


class LoadTests(SimpleTestCase):
    def test_a_model_that_cannot_load_is_reported(self):
        embeddings.load_encoder.cache_clear()
        self.addCleanup(embeddings.load_encoder.cache_clear)
        with patch.dict(os.environ, {"LEARNING_PATH_ALLOW_MODEL_DOWNLOAD": "0"}), \
                patch("sentence_transformers.SentenceTransformer", side_effect=OSError("no weights")):
            with self.assertRaises(embeddings.EncoderUnavailable):
                embeddings.load_encoder()
