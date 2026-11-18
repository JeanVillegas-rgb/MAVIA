"""A lossy network must not abort a publish on the first dropped clip.

Measured on the user's connection, 2026-09-21: the TLS handshake to
``speech.platform.bing.com`` succeeded 2 times in 10, the rest reset
immediately with WinError 10054. It is not a blocked host -- it is a lossy
one. A single attempt per clip therefore fails most of the time, and because
one failure aborts the whole publish, a teacher could never get to the end of
a lesson however many times they retried.

Failures are cheap here: a reset comes back at once rather than hanging, so
retrying costs a second, while not retrying costs the publish.
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from .services.audio_generator import (
    AudioGenerationError,
    _synthesize_text_to_mp3_with_edge,
)


class _Dropped(ConnectionResetError):
    """What aiohttp surfaces when the handshake is reset mid-connect."""


class EdgeTtsRetryTests(SimpleTestCase):
    def _synthesize(self, outcomes):
        """Run one synthesis whose attempts follow ``outcomes`` in order."""
        calls = []

        class FakeCommunicate:
            def __init__(self, text, voice):
                self.text = text

            async def save(self, path):
                result = outcomes[len(calls)]
                calls.append(path)
                if isinstance(result, Exception):
                    raise result
                Path(path).write_bytes(b"ID3 audio")

        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "clip.mp3"
            # The wait between attempts is real on a real link; here it would
            # only make the suite slow.
            with patch("edge_tts.Communicate", FakeCommunicate), patch(
                "lessons.services.audio_generator.EDGE_TTS_RETRY_DELAY", 0
            ):
                try:
                    _synthesize_text_to_mp3_with_edge("Solids keep their shape.", out)
                    error = None
                except AudioGenerationError as exc:
                    error = exc
            return len(calls), error

    def test_a_dropped_connection_is_retried_until_one_gets_through(self):
        attempts, error = self._synthesize([_Dropped("reset")] * 4 + [None])

        self.assertIsNone(error)
        self.assertEqual(attempts, 5)

    def test_the_first_attempt_is_still_the_only_one_when_it_works(self):
        attempts, error = self._synthesize([None])

        self.assertIsNone(error)
        self.assertEqual(attempts, 1)

    def test_a_connection_that_never_comes_back_still_fails(self):
        # Retrying is not the same as hanging on forever: the attempts are
        # bounded, and the teacher is told what went wrong.
        attempts, error = self._synthesize([_Dropped("reset")] * 200)

        self.assertIsNotNone(error)
        self.assertIn("Edge TTS failed", str(error))
        self.assertGreater(attempts, 1)
        self.assertLess(attempts, 200)

    def test_an_empty_narration_is_refused_without_calling_the_service(self):
        attempts, error = self._synthesize([None])
        self.assertEqual(attempts, 1)

        with self.assertRaises(AudioGenerationError):
            _synthesize_text_to_mp3_with_edge("   ", Path(tempfile.gettempdir()) / "x.mp3")
