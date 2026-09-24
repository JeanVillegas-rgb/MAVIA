"""A figure is described from the page it sits on, and adds to the lesson text.

The prompt asked the model not to restate the lesson while handing it the
document's opening under a label promising the text near the figure. On a
one-page handout those are the same thing, so a figure printed above its own
explanation was given that explanation and paraphrased it back; on a longer
PDF the label was simply untrue, and a figure on page five was judged against
page one.
"""

from django.test import SimpleTestCase

from .services import image_describer


def block(page, text, top):
    return {"page": page, "text": text, "bbox": (0.0, top, 400.0, top + 40.0)}


class NearbyLessonTextTests(SimpleTestCase):
    blocks = [
        block(1, "Solids keep a definite shape and volume.", 100),
        block(1, "Liquids take the shape of their container.", 400),
        block(2, "Melting turns a solid into a liquid.", 100),
        block(2, "Freezing turns a liquid back into a solid.", 500),
    ]

    def test_text_comes_from_the_page_the_figure_is_on(self):
        nearby = image_describer.nearby_lesson_text(
            self.blocks, page_number=2, bbox=(0.0, 80.0, 400.0, 300.0),
        )

        self.assertIn("Melting", nearby)
        self.assertNotIn("Solids keep", nearby)

    def test_the_closest_text_is_kept_when_the_budget_is_small(self):
        """A short budget must spend itself beside the figure, not at the top."""
        nearby = image_describer.nearby_lesson_text(
            self.blocks, page_number=2, bbox=(0.0, 460.0, 400.0, 480.0), limit=40,
        )

        self.assertIn("Freezing", nearby)
        self.assertNotIn("Melting", nearby)

    def test_kept_text_stays_in_reading_order(self):
        nearby = image_describer.nearby_lesson_text(
            self.blocks, page_number=1, bbox=(0.0, 380.0, 400.0, 420.0),
        )

        self.assertLess(nearby.index("Solids keep"), nearby.index("Liquids take"))

    def test_a_page_with_no_text_falls_back_rather_than_going_silent(self):
        """A full-page figure still deserves whatever context the lesson has."""
        nearby = image_describer.nearby_lesson_text(
            self.blocks, page_number=9, bbox=(0.0, 0.0, 400.0, 400.0),
            fallback="the whole lesson text",
        )

        self.assertEqual(nearby, "the whole lesson text")


class PromptTests(SimpleTestCase):
    def test_the_visual_facts_that_carry_meaning_are_asked_for(self):
        """Banning "positions" outright left only the concept to restate.

        Spacing and arrangement are what a particle diagram teaches, so they
        are the description's job, not decoration to be suppressed.
        """
        prompt = image_describer.build_prompt(lesson_title="States of matter")

        self.assertIn("arranged", prompt)
        self.assertIn("spaced", prompt)

    def test_decoration_is_still_refused(self):
        prompt = image_describer.build_prompt(lesson_title="States of matter")

        self.assertIn("colours", prompt)
        self.assertIn("Do NOT", prompt)

    def test_context_already_in_words_must_not_be_explained_again(self):
        prompt = image_describer.build_prompt(
            lesson_title="States of matter",
            nearby_text="In a solid, the particles are tightly packed together.",
        )

        self.assertIn("do not explain it again", prompt.casefold())


class SharedPageTests(SimpleTestCase):
    """Two figures on one page must not each be given the other's passage."""

    blocks = [
        block(1, "A food web shows who eats whom in a habitat.", 100),
        block(1, "The water cycle moves water between sea, air and land.", 600),
    ]

    def test_each_figure_gets_the_passage_beside_it(self):
        figures = [
            {"page_number": 1, "bbox": (0.0, 150.0, 400.0, 300.0)},
            {"page_number": 1, "bbox": (0.0, 650.0, 400.0, 800.0)},
        ]

        first = image_describer.nearby_lesson_text(
            self.blocks, page_number=1, bbox=figures[0]["bbox"], siblings=figures,
        )
        second = image_describer.nearby_lesson_text(
            self.blocks, page_number=1, bbox=figures[1]["bbox"], siblings=figures,
        )

        self.assertIn("food web", first)
        self.assertNotIn("water cycle", first)
        self.assertIn("water cycle", second)
        self.assertNotIn("food web", second)

    def test_a_lone_figure_still_gets_the_whole_page(self):
        only = [{"page_number": 1, "bbox": (0.0, 150.0, 400.0, 300.0)}]

        nearby = image_describer.nearby_lesson_text(
            self.blocks, page_number=1, bbox=only[0]["bbox"], siblings=only,
        )

        self.assertIn("food web", nearby)
        self.assertIn("water cycle", nearby)


class FallbackContextTests(SimpleTestCase):
    """Text that is not near the figure must not be treated as if it were.

    A figure printed alone on its own page -- a food web with no explanation
    beside it -- falls back to the lesson's opening. Telling the model not to
    re-explain that text would mute a description of something the fallback
    never mentioned.
    """

    def test_a_fallback_does_not_carry_the_do_not_explain_instruction(self):
        prompt = image_describer.build_prompt(
            lesson_title="Ecosystems",
            nearby_text="Plants make their own food using sunlight.",
            nearby_is_fallback=True,
        )

        self.assertIn("Plants make their own food", prompt)
        self.assertNotIn("do not explain it again", prompt)

    def test_genuinely_nearby_text_still_carries_it(self):
        prompt = image_describer.build_prompt(
            lesson_title="Ecosystems",
            nearby_text="Plants make their own food using sunlight.",
        )

        self.assertIn("do not explain it again", prompt)
