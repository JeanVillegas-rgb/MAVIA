"""Versions written from text that has since changed.

A Simplified or Elaborated version is generated from the concept's Normal text.
When that text (or its title) is edited afterwards, publishing refuses to go
ahead until a teacher checks the version. These tests pin the three ways that
check is surfaced and resolved: the flag on the review payload, "Keep as is",
and "Regenerate" -- plus publish naming the concept instead of reporting a
generic failure.
"""

import os
from unittest.mock import patch

from django.test import TestCase, override_settings

from course.models import LessonVariant
from course.variant_generator import _fingerprint, fill_missing_slots

from .models import CourseGroup, LearningMaterial, LearningObject, LearningObjectGroup, OutlineNode
from .services.topic_publish import run_topic_publish
from .tests import authenticated_api_client


@override_settings(ADAPTIVE_VARIANT_GENERATION_ENABLED=True, ADAPTIVE_VARIANT_LLM_MODEL="gemma3:4b")
@patch.dict(os.environ, {"SEMANTIC_GROUPING_MODE": "legacy"})
class StaleVersionTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.node = OutlineNode.objects.create(course=self.course, title="Matter", order=0, depth=0)
        self.material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.node, title="Lesson 1",
            generated_json={"learning_objects_confirmed": True},
        )
        group = LearningObjectGroup.objects.create(outline_node=self.node, label="Solid")
        self.solid = LearningObject.objects.create(
            material=self.material, group=group, title="Key properties of solids",
            content="A solid has a fixed shape and a fixed volume.", order=0,
        )
        fingerprint = _fingerprint(self.solid)
        self.simplified = LessonVariant.objects.create(
            learning_object=self.solid, variant="SIMPLIFIED", narration="Solids keep their shape.",
            origin=LessonVariant.Origin.GENERATED, source_fingerprint=fingerprint,
        )
        self.elaborated = LessonVariant.objects.create(
            learning_object=self.solid, variant="ELABORATED", narration="A solid keeps a fixed shape and volume.",
            origin=LessonVariant.Origin.GENERATED, source_fingerprint=fingerprint,
        )
        self.client_api = authenticated_api_client()
        self.base = f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}"

    def _edit_normal_text(self):
        self.solid.title = "Solids"
        self.solid.save()

    def _slots(self):
        response = self.client_api.get(f"{self.base}/learning-resources/")
        group = next(row for row in response.data["learning_object_groups"] if row["versions"]["representative_id"] == self.solid.id)
        return group["versions"]["slots"]

    def test_versions_are_not_flagged_while_the_text_is_unchanged(self):
        slots = self._slots()

        self.assertFalse(slots["simplified"]["stale"])
        self.assertFalse(slots["elaborated"]["stale"])

    def test_editing_the_normal_text_flags_both_generated_versions(self):
        self._edit_normal_text()

        slots = self._slots()

        self.assertTrue(slots["simplified"]["stale"])
        self.assertTrue(slots["elaborated"]["stale"])

    def test_text_from_another_pdf_is_never_flagged(self):
        """It was not written from this object's wording, so an edit here says
        nothing about whether it still fits."""
        self.simplified.origin = LessonVariant.Origin.SOURCE_PDF
        self.simplified.source_fingerprint = ""
        self.simplified.save()
        self._edit_normal_text()

        self.assertFalse(self._slots()["simplified"]["stale"])

    def test_keep_as_is_clears_the_flag_without_changing_the_wording(self):
        self._edit_normal_text()

        response = self.client_api.post(f"{self.base}/versions/{self.simplified.id}/keep/")

        self.assertEqual(response.status_code, 200, response.data)
        self.simplified.refresh_from_db()
        self.assertEqual(self.simplified.narration, "Solids keep their shape.")
        self.assertEqual(self.simplified.assigned_by, LessonVariant.AssignedBy.TEACHER)
        self.assertFalse(self._slots()["simplified"]["stale"])
        # The other version is still the teacher's to check.
        self.assertTrue(self._slots()["elaborated"]["stale"])

    def test_keeping_both_lets_publishing_settle_the_concept(self):
        self._edit_normal_text()
        self.assertTrue(fill_missing_slots(self.solid)["errors"])

        for variant in (self.simplified, self.elaborated):
            self.client_api.post(f"{self.base}/versions/{variant.id}/keep/")

        self.solid.refresh_from_db()
        self.assertEqual(fill_missing_slots(self.solid)["errors"], [])

    @patch("course.variant_generator._request_variants")
    def test_regenerate_replaces_only_the_chosen_version(self, request_variants):
        request_variants.return_value = {"SIMPLIFIED": "A solid stays the same shape.", "ELABORATED": "unused"}
        self._edit_normal_text()

        response = self.client_api.post(
            f"{self.base}/learning-objects/{self.solid.id}/generate-versions/",
            {"slot": "SIMPLIFIED", "replace_stale": True},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["version_generation"]["generated"], ["SIMPLIFIED"])
        simplified = LessonVariant.objects.get(learning_object=self.solid, variant="SIMPLIFIED")
        self.assertEqual(simplified.narration, "A solid stays the same shape.")
        self.assertFalse(self._slots()["simplified"]["stale"])
        self.elaborated.refresh_from_db()
        self.assertEqual(self.elaborated.narration, "A solid keeps a fixed shape and volume.")

    def test_plain_generate_still_refuses_to_overwrite_an_out_of_date_version(self):
        self._edit_normal_text()

        response = self.client_api.post(
            f"{self.base}/learning-objects/{self.solid.id}/generate-versions/",
            {"slot": "SIMPLIFIED"},
            format="json",
        )

        self.assertEqual(response.data["version_generation"]["errors"][0]["slots"], ["SIMPLIFIED"])
        self.simplified.refresh_from_db()
        self.assertEqual(self.simplified.narration, "Solids keep their shape.")

    @patch("lessons.services.topic_publish.generate_version_audio", return_value={"generated_count": 0})
    @patch("lessons.services.topic_publish.populate_missing_image_descriptions", return_value={"generated_count": 0, "errors": []})
    @patch("lessons.services.topic_publish.generate_material_audio_playlist", return_value={"generated_count": 0})
    def test_publish_names_the_concept_that_needs_checking(self, audio, images, version_audio):
        self._edit_normal_text()
        events = []

        summary = run_topic_publish(
            self.course, self.node, set_confirmed=lambda material: None,
            on_event=lambda event_type, message, **data: events.append((event_type, message, data)),
        )

        self.assertFalse(summary["published"])
        failures = [event for event in events if event[0] == "versions_failed"]
        self.assertEqual(len(failures), 1)
        self.assertIn("Solids", failures[0][1])
        self.assertEqual(failures[0][2]["slots"], ["ELABORATED", "SIMPLIFIED"])
