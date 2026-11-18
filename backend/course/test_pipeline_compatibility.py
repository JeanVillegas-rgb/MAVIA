import json
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from lessons.models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
)
from lessons.services.topic_publish import concepts_missing_a_version

from .models import LessonVariant
from .services import _build_chunk
from .generate_variation import fill_missing_bundle_slots
from .version_assignment import assign_group_versions


@override_settings(
    CONTENT_VERSION_LLM_ENABLED=True,
    ADAPTIVE_VARIANT_GENERATION_ENABLED=True,
    CONTENT_VERSION_LLM_MODEL="test-model",
    CONTENT_VERSION_LLM_TIMEOUT=10,
    ADAPTIVE_VARIANT_LLM_MODEL="test-model",
    ADAPTIVE_VARIANT_TIMEOUT=10,
)
class PipelineCompatibilityTests(TestCase):
    def setUp(self):
        course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=course, title="Matter")
        confirmed = {"learning_objects_confirmed": True}
        self.standard_material = LearningMaterial.objects.create(
            course=course, outline_node=self.topic, title="PDF 1",
            generated_json=dict(confirmed), status=LearningMaterial.Status.COMPLETED,
        )
        self.candidate_material = LearningMaterial.objects.create(
            course=course, outline_node=self.topic, title="PDF 2",
            generated_json=dict(confirmed), status=LearningMaterial.Status.COMPLETED,
        )
        now = timezone.now()
        LearningMaterial.objects.filter(pk=self.standard_material.pk).update(created_at=now)
        LearningMaterial.objects.filter(pk=self.candidate_material.pk).update(
            created_at=now + timedelta(minutes=1)
        )
        self.group = LearningObjectGroup.objects.create(
            outline_node=self.topic, label="Matter",
        )
        self.standard = LearningObject.objects.create(
            material=self.standard_material, group=self.group, title="Matter",
            content="Matter has mass and occupies space.", order=0,
        )
        self.candidate = LearningObject.objects.create(
            material=self.candidate_material, group=self.group, title="Matter",
            content="Matter is anything that has mass and takes up space around us.", order=0,
        )

    @patch("course.version_assignment.review_gemma_role")
    @patch("course.bundle_version_classifier.groq_generate")
    def test_classification_populates_mavia_version_selection(self, generate, review):
        LearningObject.objects.create(
            material=self.standard_material, group=self.group, title="Matter example",
            content="A book is matter.", order=1,
        )
        LearningObject.objects.create(
            material=self.candidate_material, group=self.group, title="Matter example",
            content="A chair is matter.", order=1,
        )
        generate.return_value = (
            json.dumps({"level": "simplified", "reason": "Uses clearer wording."}),
            {},
        )
        review.return_value = {
            "accepted": True, "slot": "SIMPLIFIED", "issues": [],
            "concerns": [], "measures": {},
        }

        result = assign_group_versions(self.group, use_llm=True)
        self.group.refresh_from_db()

        self.assertTrue(result["classification_complete"])
        self.assertEqual(
            self.group.version_selection["bundle_roles"][str(self.candidate_material.id)],
            "SIMPLIFIED",
        )
        self.assertEqual(
            self.group.version_selection["standard_material_id"],
            self.standard_material.id,
        )
        comparison = json.loads(generate.call_args.args[0].splitlines()[-1])
        self.assertEqual(
            comparison["normal_baseline"],
            "Matter has mass and occupies space.\nA book is matter.",
        )
        self.assertEqual(
            comparison["candidate_bundle"],
            "Matter is anything that has mass and takes up space around us.\n"
            "A chair is matter.",
        )

    @patch("course.generate_variation.groq_generate")
    def test_generation_creates_mavia_lesson_variant_rows(self, generate):
        # Mark classification complete with an elaborated source bundle so only
        # Simplified remains missing.
        with patch("course.version_assignment.classify_group_versions") as classify, patch(
            "course.version_assignment.review_gemma_role"
        ) as review:
            classify.return_value = {
                self.candidate.id: {
                    "slot": "ELABORATED", "confidence": 1.0,
                    "reason": "Adds explanation.",
                    "evidence": {"simplifications": [], "additions": [], "problems": []},
                }
            }
            review.return_value = {
                "accepted": True, "slot": "ELABORATED", "issues": [],
                "concerns": [], "measures": {},
            }
            assign_group_versions(self.group, use_llm=True)

        generate.side_effect = [
            (json.dumps({"content": "Matter is stuff that takes space.", "reason": "Clearer."}), {}),
            (json.dumps({"content": "Matter has mass and occupies physical space.", "reason": "Fuller."}), {}),
        ]
        result = fill_missing_bundle_slots(self.group)

        self.assertIn("SIMPLIFIED", result["generated"])
        row = LessonVariant.objects.get(
            learning_object=self.standard, variant="SIMPLIFIED",
        )
        self.assertEqual(row.origin, LessonVariant.Origin.GENERATED)
        self.assertEqual(row.narration, "Matter is stuff that takes space.")

    @patch("course.generate_variation.groq_generate")
    def test_complete_bundle_is_served_from_one_marked_row(self, generate):
        LearningObject.objects.create(
            material=self.standard_material, group=self.group,
            title="Example", content="A book is matter.", order=1,
        )
        generate.side_effect = [
            (json.dumps({"content": "Matter takes space, like a book.", "reason": "Clearer."}), {}),
            (json.dumps({"content": "Matter has mass and occupies space; a book is one example.", "reason": "Fuller."}), {}),
        ]
        result = fill_missing_bundle_slots(self.group)
        self.assertEqual(set(result["generated"]), {"SIMPLIFIED", "ELABORATED"})
        self.assertEqual(LessonVariant.objects.count(), 2)
        self.assertTrue(all(row.generator_model.startswith("jean-bundle:")
                            for row in LessonVariant.objects.all()))
        chunk = _build_chunk(self.standard)
        self.assertTrue(chunk["versions_complete"])
        self.assertEqual(chunk["variants"]["simplified"]["text"],
                         "Matter takes space, like a book.")
        self.assertEqual(concepts_missing_a_version(
            self.topic, [self.standard_material, self.candidate_material],
        ), [])

    def test_older_partial_row_does_not_count_as_complete_bundle(self):
        LearningObject.objects.create(
            material=self.standard_material, group=self.group,
            title="Example", content="A book is matter.", order=1,
        )
        LessonVariant.objects.create(
            learning_object=self.standard, variant="SIMPLIFIED",
            narration="Only the first object.", origin=LessonVariant.Origin.GENERATED,
        )
        self.assertNotIn("simplified", _build_chunk(self.standard)["variants"])
        self.assertIn(self.standard.id, concepts_missing_a_version(
            self.topic, [self.standard_material, self.candidate_material],
        ))
        result = fill_missing_bundle_slots(self.group, target_slots=["SIMPLIFIED"])
        self.assertEqual(result["generated"], [])
        self.assertEqual(result["errors"][0]["slots"], ["SIMPLIFIED"])
