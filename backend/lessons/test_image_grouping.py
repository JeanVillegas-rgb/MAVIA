"""A figure is grouped automatically only when a label corroborates it.

Two AI-written figure descriptions share stock phrasing, so the cross-encoder
scores them high against each other whatever they depict. Measured live on
topic 152: one PDF's particle figure was auto-grouped with two unrelated
figures of the other PDF, at 0.649 and 0.627. Wording alone may therefore
raise a card for the teacher, never place a figure on its own.
"""

import os
from unittest.mock import patch

from django.test import TestCase

from .models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    LearningObjectMatchSuggestion,
    OutlineNode,
)
from .services import semantic_grouping as semantic
from .services.learning_resource_linker import refresh_learning_object_match_suggestions
from .test_label_corroboration import LABEL_ENV
from .test_semantic_grouping import FakeRuntime


STOCK = (
    "This figure shows the arrangement of particles and helps the student "
    "understand how the three states of matter differ from one another."
)
OTHER_STOCK = (
    "This figure shows how the particles are arranged and helps the student "
    "understand the differences between the three states of matter."
)


class ImageAutoGroupingTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="States of matter")
        self.source_material = self.material("Lesson one")
        self.other_material = self.material("Lesson two")
        self.source_group = LearningObjectGroup.objects.create(outline_node=self.topic)
        self.other_group = LearningObjectGroup.objects.create(outline_node=self.topic)

    def material(self, title):
        return LearningMaterial.objects.create(
            course=self.course,
            outline_node=self.topic,
            title=title,
            generated_json={"learning_objects_confirmed": True},
        )

    def figure(self, material, group, title, content):
        return LearningObject.objects.create(
            material=material,
            group=group,
            title=title,
            content=content,
            kind=LearningObject.Kind.IMAGE,
        )

    def decision(self, source, scores):
        with patch.object(semantic, "runtime", return_value=FakeRuntime(scores)):
            return semantic.semantic_decision(
                source.material,
                source.title,
                source.content,
                source.kind,
                source.order,
                source.section_title,
                source.id,
            )

    @patch.dict(os.environ, LABEL_ENV)
    def test_unrelated_figures_scoring_above_auto_raise_a_card_not_a_group(self):
        source = self.figure(
            self.source_material, self.source_group, "Particle arrangement", STOCK,
        )
        other = self.figure(
            self.other_material, self.other_group,
            "Changing From One State to Another", OTHER_STOCK,
        )
        scores = {other.content: .95}

        decision = self.decision(source, scores)

        self.assertEqual(decision["confidence"], "medium")
        self.assertFalse(decision["evidence"]["auto_eligible"])
        self.assertTrue(decision["evidence"]["image_needs_label_corroboration"])

        with patch.object(semantic, "runtime", return_value=FakeRuntime(scores)):
            refresh_learning_object_match_suggestions(self.source_material)

        source.refresh_from_db()
        self.assertEqual(source.group_id, self.source_group.id)
        self.assertTrue(
            LearningObjectMatchSuggestion.objects.filter(status="pending").exists()
        )

    @patch.dict(os.environ, LABEL_ENV)
    def test_a_corroborating_title_still_groups_the_figures_automatically(self):
        source = self.figure(
            self.source_material, self.source_group, "Particle arrangement", STOCK,
        )
        other = self.figure(
            self.other_material, self.other_group, "Particle arrangements", OTHER_STOCK,
        )
        scores = {other.content: .95}

        decision = self.decision(source, scores)

        self.assertEqual(decision["confidence"], "high")
        self.assertTrue(decision["evidence"]["label_corroborated"])

        with patch.object(semantic, "runtime", return_value=FakeRuntime(scores)):
            refresh_learning_object_match_suggestions(self.source_material)

        source.refresh_from_db()
        self.assertEqual(source.group_id, self.other_group.id)
        self.assertFalse(
            LearningObjectMatchSuggestion.objects.filter(status="pending").exists()
        )

    @patch.dict(os.environ, LABEL_ENV)
    def test_the_same_author_caption_groups_figures_named_only_figure_1(self):
        """Measured live: two PDFs' flower diagrams, both "Figure 1", caption
        identical, scored 0.996 -- and waited on a teacher, because "Figure 1"
        is too generic to corroborate anything."""
        caption = "Figure 1. The main parts of a flower involved in reproduction."
        source = self.figure(self.source_material, self.source_group, "Figure 1", caption)
        other = self.figure(self.other_material, self.other_group, "Figure 1", caption)
        scores = {other.content: .996}

        decision = self.decision(source, scores)

        self.assertEqual(decision["confidence"], "high")
        self.assertTrue(decision["evidence"]["caption_corroborated"])

        with patch.object(semantic, "runtime", return_value=FakeRuntime(scores)):
            refresh_learning_object_match_suggestions(self.source_material)

        source.refresh_from_db()
        self.assertEqual(source.group_id, self.other_group.id)
        self.assertFalse(
            LearningObjectMatchSuggestion.objects.filter(status="pending").exists()
        )

    @patch.dict(os.environ, LABEL_ENV)
    def test_narrated_figures_are_matched_by_their_recorded_caption(self):
        """Once narrated, a figure's text is the model's description, not its
        caption; the caption extraction recorded for the image still matches."""
        caption = "Figure 1. The main parts of a flower involved in reproduction."
        source = self.narrated_figure(self.source_material, self.source_group, caption,
                                      "The image shows a flower with several distinct parts.")
        other = self.narrated_figure(self.other_material, self.other_group, caption,
                                     "The image shows the key components of a flower.")

        decision = self.decision(source, {other.content: .9})

        self.assertEqual(decision["confidence"], "high")
        self.assertTrue(decision["evidence"]["caption_corroborated"])

    def narrated_figure(self, material, group, caption, narration):
        url = f"/media/extracted_images/mat_{material.id}_img_1.png"
        records = material.generated_json.setdefault("image_descriptions", [])
        records.append({"image_url": url, "caption": caption})
        material.save(update_fields=["generated_json"])
        return LearningObject.objects.create(
            material=material, group=group, title=narration, content=narration,
            kind=LearningObject.Kind.IMAGE, image_url=url,
        )

    @patch.dict(os.environ, LABEL_ENV)
    def test_different_captions_still_raise_a_card(self):
        source = self.figure(
            self.source_material, self.source_group, "Figure 1",
            "Figure 1. The main parts of a flower involved in reproduction.",
        )
        other = self.figure(
            self.other_material, self.other_group, "Figure 1",
            "Figure 1. How pollen travels from one flower to another.",
        )

        decision = self.decision(source, {other.content: .95})

        self.assertEqual(decision["confidence"], "medium")
        self.assertTrue(decision["evidence"]["image_needs_label_corroboration"])

    @patch.dict(os.environ, LABEL_ENV)
    def test_a_caption_too_short_to_be_specific_corroborates_nothing(self):
        source = self.figure(self.source_material, self.source_group, "Figure 2", "Figure 2. A flower.")
        other = self.figure(self.other_material, self.other_group, "Figure 2", "Figure 2. A flower.")

        decision = self.decision(source, {other.content: .95})

        self.assertEqual(decision["confidence"], "medium")

    @patch.dict(os.environ, LABEL_ENV)
    def test_an_ai_description_is_not_a_caption(self):
        """Stock AI phrasing is exactly what the figure gate exists to catch."""
        source = self.figure(self.source_material, self.source_group, "Figure 1", STOCK)
        other = self.figure(self.other_material, self.other_group, "Figure 1", STOCK)

        decision = self.decision(source, {other.content: .95})

        self.assertEqual(decision["confidence"], "medium")

    @patch.dict(os.environ, LABEL_ENV)
    def test_text_objects_are_unaffected(self):
        source = LearningObject.objects.create(
            material=self.source_material, group=self.source_group,
            title="Solid", content="A solid keeps a fixed shape and a fixed volume.",
        )
        other = LearningObject.objects.create(
            material=self.other_material, group=self.other_group,
            title="Changing state", content="Heating a solid melts it into a liquid.",
        )

        decision = self.decision(source, {other.content: .95})

        self.assertEqual(decision["confidence"], "high")
        self.assertNotIn("image_needs_label_corroboration", decision["evidence"])

    # The labels printed inside a figure are the author's, like a caption.

    def labelled_figure(self, material, group, title, visible_text, narration=STOCK):
        url = f"/media/extracted_images/mat_{material.id}_img_{title[:5]}.png"
        records = material.generated_json.setdefault("image_descriptions", [])
        records.append({"image_url": url, "caption": "", "visible_text": visible_text})
        material.save(update_fields=["generated_json"])
        return LearningObject.objects.create(
            material=material, group=group, title=title, content=narration,
            kind=LearningObject.Kind.IMAGE, image_url=url,
        )

    @patch.dict(os.environ, LABEL_ENV)
    def test_the_same_printed_labels_group_the_figures(self):
        labels = "Evaporation, Condensation, Precipitation, Collection"
        source = self.labelled_figure(self.source_material, self.source_group, "Solids - figure", labels)
        other = self.labelled_figure(
            self.other_material, self.other_group, "Water cycle - figure", labels, OTHER_STOCK,
        )

        decision = self.decision(source, {other.content: .95})

        self.assertEqual(decision["confidence"], "high")
        self.assertTrue(decision["evidence"]["figure_text_corroborated"])

    @patch.dict(os.environ, LABEL_ENV)
    def test_labels_extracted_with_a_stray_word_still_agree(self):
        source = self.labelled_figure(
            self.source_material, self.source_group, "A - figure",
            "Evaporation Condensation Precipitation Collection Runoff",
        )
        other = self.labelled_figure(
            self.other_material, self.other_group, "B - figure",
            "Evaporation Condensation Precipitation Collection Runoff Sun", OTHER_STOCK,
        )

        decision = self.decision(source, {other.content: .95})

        self.assertEqual(decision["confidence"], "high")

    @patch.dict(os.environ, LABEL_ENV)
    def test_a_few_common_labels_corroborate_nothing(self):
        # "Solid, Liquid, Gas" labels half the figures of this topic.
        source = self.labelled_figure(self.source_material, self.source_group, "A - figure", "Solid Liquid Gas")
        other = self.labelled_figure(
            self.other_material, self.other_group, "B - figure", "Solid Liquid Gas", OTHER_STOCK,
        )

        decision = self.decision(source, {other.content: .95})

        self.assertEqual(decision["confidence"], "medium")

    @patch.dict(os.environ, LABEL_ENV)
    def test_different_printed_labels_still_raise_a_card(self):
        source = self.labelled_figure(
            self.source_material, self.source_group, "A - figure",
            "Evaporation Condensation Precipitation Collection",
        )
        other = self.labelled_figure(
            self.other_material, self.other_group, "B - figure",
            "Melting Freezing Boiling Condensation Sublimation", OTHER_STOCK,
        )

        decision = self.decision(source, {other.content: .95})

        self.assertEqual(decision["confidence"], "medium")

    @patch.dict(os.environ, LABEL_ENV)
    def test_figures_sharing_only_a_generated_section_name_raise_a_card(self):
        source = self.figure(self.source_material, self.source_group, "Solids - figure", STOCK)
        other = self.figure(self.other_material, self.other_group, "Solids - figure", OTHER_STOCK)

        decision = self.decision(source, {other.content: .95})

        self.assertEqual(decision["confidence"], "medium")
        self.assertNotIn("label_corroborated", decision["evidence"])
