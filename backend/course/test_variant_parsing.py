"""Salvaging a reply Gemma meant to be JSON but did not quite write as JSON.

Recorded from the real run that blocked publishing topic 152 on 2026-09-21.
The concept "Comparing the Three States" failed all three attempts, every
time in the same way: both variants come back correct and within their word
limits, but the model closes the ``elaborated`` string with a typographic
right quote (U+201D) instead of ``"``. The string therefore never terminates,
the grammar-constrained decoding never sees the object close, generation runs
to ``num_predict`` -- ``done_reason`` comes back ``length`` -- and the tail
fills with the word limits echoed back out of the prompt.

Nothing here rewrites what the model said. The delimiters are repaired only
when doing so yields JSON that parses; a reply carrying genuine curly quotes
inside its text does not parse after the substitution and is still rejected.
"""

from django.test import SimpleTestCase

from .variant_generator import VariantGenerationError, _parse_response


# The first attempt's reply, byte for byte, truncated after the repetition
# establishes itself. `rawlen` was 2222 and every attempt looked like this.
GEMMA_SMART_QUOTE_REPLY = (
    '{"simplified": "The table compares the three states. The comparison uses '
    'text labels for rows and columns, not color.", "elaborated": "The table '
    'presents a comparison of three states. The table’s structure utilizes '
    'text labels for both the rows and columns to ensure the comparison is '
    'clear.”} 51 words. 48 words. 27 words. 48 words. 27 words. 51 words. '
    '48 words. 27 words. 48 words. 27 words. 51 words. 48 words. 27 words.'
)


class SmartQuoteReplyTests(SimpleTestCase):
    def test_a_reply_closed_with_a_typographic_quote_is_recovered(self):
        parsed = _parse_response(GEMMA_SMART_QUOTE_REPLY, source_word_count=24)

        self.assertEqual(
            parsed["SIMPLIFIED"],
            "The table compares the three states. The comparison uses text labels "
            "for rows and columns, not color.",
        )
        # The apostrophe inside the sentence is the model's own wording and is
        # left exactly as it wrote it; only the delimiter was repaired.
        self.assertEqual(
            parsed["ELABORATED"],
            "The table presents a comparison of three states. The table’s structure "
            "utilizes text labels for both the rows and columns to ensure the "
            "comparison is clear.",
        )

    def test_the_echoed_word_limits_are_not_carried_into_the_lesson(self):
        parsed = _parse_response(GEMMA_SMART_QUOTE_REPLY, source_word_count=24)

        for text in parsed.values():
            self.assertNotIn("words.", text)

    def test_the_recovered_variants_still_face_the_grounding_limits(self):
        # Recovery is not an exemption: a salvaged reply is checked like any
        # other, so an over-long elaboration is still refused.
        overlong = (
            '{"simplified": "A solid keeps its shape.", "elaborated": "'
            + "unsupported extra wording " * 20
            + '”}'
        )
        with self.assertRaises(VariantGenerationError):
            _parse_response(overlong, source_word_count=10)

    def test_a_reply_that_is_simply_not_json_is_still_refused(self):
        with self.assertRaises(VariantGenerationError):
            _parse_response("I could not write versions of that.", source_word_count=24)

    def test_genuine_curly_quotes_inside_the_text_are_left_alone(self):
        """Repair must not turn a quotation in the wording into a delimiter.

        Substituting here would produce unescaped quotes and invalid JSON, so
        the reply is refused rather than silently truncated to "He said ".
        """
        quoted = (
            '{"simplified": "He said “hello” to the class.", '
            '"elaborated": "He said “hello” to the whole class that morning."}'
        )
        parsed = _parse_response(quoted, source_word_count=24)

        self.assertEqual(parsed["SIMPLIFIED"], "He said “hello” to the class.")
