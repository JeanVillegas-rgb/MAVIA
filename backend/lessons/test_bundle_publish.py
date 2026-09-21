"""Publishing a topic whose versions come from two PDFs.

A version a PDF supplies stores no ``LessonVariant`` row -- its text is its own
objects -- so the publish gate has to read the concept's bundles instead of
counting rows, and the audio pipeline has to speak objects the lesson playlist
deliberately leaves out. Both were written for one-PDF topics and are exercised
here end to end: the point of this file is that a real two-PDF topic publishes,
with a learning path, and that no version reaches a blind learner in silence.
"""

import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from course.models import LessonVariant
from course.services import _build_chunk
from course.version_assignment import set_bundle_role
from learning_path.models import LearningPathStep

from .models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
)
from .services.topic_publish import concepts_missing_a_version, run_topic_publish


SOLID_A = (
    "A solid is a state of matter that keeps a fixed shape and a fixed volume. "
    "Its particles are packed tightly together in a regular arrangement."
)
SOLID_B = "A solid keeps its shape. The bits inside it are packed really tight."
LIQUID_A = (
    "A liquid keeps a fixed volume but takes the shape of whatever container "
    "holds it, because its particles can slide past one another."
)


@override_settings(
    ADAPTIVE_VARIANT_GENERATION_ENABLED=True,
    ADAPTIVE_VARIANT_LLM_MODEL="gemma3:4b",
)
class TwoPdfPublishTests(TestCase):
    """One PDF supplies Simplified; the other is the Normal wording."""

    def setUp(self):
        media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media_root, ignore_errors=True)
        media = override_settings(MEDIA_ROOT=media_root, MEDIA_URL="/media/")
        media.enable()
        self.addCleanup(media.disable)

        def fake_tts(text, output_path_without_suffix):
            path = Path(output_path_without_suffix).with_suffix(".mp3")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"audio")
            return path

        tts = patch("lessons.services.audio_generator.synthesize_text_to_audio", fake_tts)
        tts.start()
        self.addCleanup(tts.stop)

        images = patch("lessons.services.topic_publish.populate_missing_image_descriptions")
        self.images = images.start()
        self.images.return_value = {"generated_count": 0, "errors": []}
        self.addCleanup(images.stop)

        variants = patch("course.variant_generator._request_variants")
        self.request_variants = variants.start()
        self.request_variants.return_value = {
            "SIMPLIFIED": "Written simply.",
            "ELABORATED": "Written at length, with more detail than the original.",
        }
        self.addCleanup(variants.stop)

        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.node = OutlineNode.objects.create(
            course=self.course, title="Matter", order=0, depth=0
        )
        now = timezone.now()
        self.first = self._material("Lesson 1", now)
        self.second = self._material("Lesson 1 simplified", now + timezone.timedelta(minutes=5))

        self.solid = LearningObjectGroup.objects.create(outline_node=self.node, label="Solid")
        self.solid_a = self._object(self.first, self.solid, "Solid", SOLID_A, 0)
        self.solid_b = self._object(self.second, self.solid, "Solid", SOLID_B, 0)
        self.liquid = LearningObjectGroup.objects.create(outline_node=self.node, label="Liquid")
        self.liquid_a = self._object(self.first, self.liquid, "Liquid", LIQUID_A, 1)

        classifier = patch("course.version_assignment.classify_group_versions")
        self.classify = classifier.start()
        self.addCleanup(classifier.stop)

        def classify(members, representative=None):
            # The first-uploaded PDF is the Normal wording; the other is the
            # Simplified one, supplied as its own objects.
            simple_ids = {self.solid_b.id}
            return {
                item.id: {
                    "slot": "SIMPLIFIED" if item.id in simple_ids else "ORIGINAL",
                    "confidence": 0.95,
                    "reason": "Plainer wording." if item.id in simple_ids else "Balanced.",
                }
                for item in members
            }

        self.classify.side_effect = classify
        self.events = []

    def _material(self, title, created_at):
        material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.node, title=title,
            generated_json={"learning_objects_confirmed": True},
        )
        LearningMaterial.objects.filter(pk=material.pk).update(created_at=created_at)
        material.refresh_from_db()
        return material

    def _object(self, material, group, title, content, order):
        return LearningObject.objects.create(
            material=material, group=group, title=title, content=content, order=order,
        )

    def _record(self, event_type, message, **data):
        self.events.append((event_type, message, data))

    def _run(self):
        return run_topic_publish(
            self.course, self.node, set_confirmed=lambda material: None,
            on_event=self._record,
        )

    def test_a_two_pdf_topic_publishes_with_a_learning_path(self):
        summary = self._run()

        self.assertEqual(summary["incomplete_versions"], [])
        self.assertTrue(summary["published"], summary)
        self.node.refresh_from_db()
        self.assertTrue(self.node.published)
        self.assertIsNotNone(summary["learning_path"])
        self.assertEqual(summary["learning_path"]["steps"], 2)
        self.assertEqual(LearningPathStep.objects.filter(outline_node=self.node).count(), 2)

    def test_a_pdf_supplied_version_is_served_with_text_and_audio(self):
        self._run()

        self.solid_a.refresh_from_db()
        simplified = _build_chunk(self.solid_a)["variants"]["simplified"]
        self.assertEqual(simplified["origin"], LessonVariant.Origin.SOURCE_PDF)
        self.assertTrue(simplified["text"].strip())
        self.assertTrue(simplified["segments"])
        for segment in simplified["segments"]:
            self.assertTrue(segment["text"].strip(), simplified)
            # A blind learner switching to Simplified would otherwise hear
            # nothing at all, and nothing would report an error.
            self.assertTrue(segment["audio_url"], simplified)

    def test_every_version_of_every_concept_has_text_and_audio(self):
        self._run()

        for lead in (self.solid_a, self.liquid_a):
            lead.refresh_from_db()
            chunk = _build_chunk(lead)
            self.assertTrue(chunk["versions_complete"], chunk)
            for role in ("normal", "simplified", "elaborated"):
                version = chunk["variants"][role]
                self.assertTrue(version["text"].strip(), (lead.title, role))
                for segment in version["segments"]:
                    self.assertTrue(segment["audio_url"], (lead.title, role, version))

    def test_no_object_is_left_pointing_out_of_its_concept(self):
        self._run()

        for item in LearningObject.objects.filter(material__outline_node=self.node):
            if item.represented_by_id is None:
                continue
            self.assertEqual(
                item.represented_by.group_id, item.group_id,
                f"{item.title} is taught through an object outside its concept",
            )

    def test_a_generated_role_is_still_required_where_no_pdf_supplies_it(self):
        from course.variant_generator import VariantGenerationError

        self.request_variants.side_effect = VariantGenerationError("no JSON")

        summary = self._run()

        # Simplified comes from the second PDF, so only the concept with no
        # second PDF at all is reported -- and Solid's missing Elaborated.
        self.assertFalse(summary["published"])
        self.assertIn(self.liquid_a.id, summary["incomplete_versions"])
        self.assertNotIn(self.solid_b.id, summary["incomplete_versions"])


class PendingBundleGateTests(TestCase):
    """A bundle nobody has ruled on yet is not a version, and not a gap."""

    def setUp(self):
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.node = OutlineNode.objects.create(
            course=self.course, title="Matter", order=0, depth=0
        )
        now = timezone.now()
        self.first = self._material("Lesson 1", now)
        self.second = self._material("Lesson 2", now + timezone.timedelta(minutes=5))
        self.group = LearningObjectGroup.objects.create(outline_node=self.node, label="Solid")
        self.normal = LearningObject.objects.create(
            material=self.first, group=self.group, title="Solid", content=SOLID_A, order=0,
        )
        self.awaiting = LearningObject.objects.create(
            material=self.second, group=self.group, title="Solid", content=SOLID_B, order=0,
        )
        self.group.version_selection = {"normal_material_id": self.first.id}
        self.group.save(update_fields=["version_selection"])
        set_bundle_role(self.group, self.second.id, "SIMPLIFIED")
        for slot in ("SIMPLIFIED", "ELABORATED"):
            LessonVariant.objects.create(
                learning_object=self.normal, variant=slot,
                narration=f"{slot} wording.", origin=LessonVariant.Origin.GENERATED,
            )

    def _material(self, title, created_at):
        material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.node, title=title,
            generated_json={"learning_objects_confirmed": True},
        )
        LearningMaterial.objects.filter(pk=material.pk).update(created_at=created_at)
        material.refresh_from_db()
        return material

    def test_an_unconfirmed_bundle_does_not_block_publishing(self):
        # Unconfirmed, so ``settle_group`` left it unrepresented: it is still
        # a teaching step of its own, and the review screen still asks about
        # it. What it is not is a concept missing a version.
        self.assertIsNone(self.awaiting.represented_by_id)

        missing = concepts_missing_a_version(self.node, [self.first, self.second])

        self.assertEqual(missing, [])

    def test_a_concept_with_no_text_at_all_is_still_reported(self):
        LearningObject.objects.filter(pk=self.awaiting.pk).delete()
        LearningObject.objects.filter(pk=self.normal.pk).update(content="")

        missing = concepts_missing_a_version(self.node, [self.first, self.second])

        self.assertEqual(missing, [self.normal.id])

    def test_a_role_supplied_by_an_unconfirmed_pdf_is_not_supplied(self):
        """Finding 11: the gate counted a role supplied by any material of the
        concept, but the audio phase only covers the publish's confirmed
        materials. Un-confirming the second PDF after grouping would publish a
        Simplified track with nothing to play."""
        LessonVariant.objects.filter(
            learning_object=self.normal, variant="SIMPLIFIED",
        ).delete()

        missing = concepts_missing_a_version(self.node, [self.first])

        self.assertEqual(missing, [self.normal.id])

    def test_a_role_supplied_by_a_confirmed_pdf_still_counts(self):
        LessonVariant.objects.filter(
            learning_object=self.normal, variant="SIMPLIFIED",
        ).delete()

        missing = concepts_missing_a_version(self.node, [self.first, self.second])

        self.assertEqual(missing, [])
