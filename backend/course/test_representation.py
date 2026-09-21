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

from .models import CourseModule, LessonNode, LessonVariant
from .services import LessonPackageService, _build_chunk
from .version_assignment import (
    assign_group_versions,
    bundle_roles,
    set_bundle_role,
    release_from_group,
    release_learning_object,
    settle_group,
)


SHORT = "Solid has a fixed shape. It holds its form. It does not flow."
LONG = (
    "A solid is a state of matter that maintains a fixed shape and a fixed volume. "
    "The particles inside it are packed tightly together in a regular arrangement. "
    "Because those particles cannot move past one another, a solid does not flow."
)
MIDDLING = "A solid keeps its shape. The particles are packed closely. It will not flow away."


@override_settings(
    ADAPTIVE_VARIANT_GENERATION_ENABLED=True,
    ADAPTIVE_VARIANT_LLM_MODEL="gemma3:4b",
)
class RepresentationTests(TestCase):
    def setUp(self):
        classifier_patcher = patch("course.version_assignment.classify_group_versions")
        self.classify_group_versions = classifier_patcher.start()
        self.addCleanup(classifier_patcher.stop)
        def classify(members, representative=None):
            original = representative or members[0]
            return {
                item.id: {
                    "slot": "ORIGINAL" if item.id == original.id else "ELABORATED",
                    "confidence": 0.95,
                    "reason": "Balanced original." if item.id == original.id else "More detailed.",
                }
                for item in members
            }
        self.classify_group_versions.side_effect = classify
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.node = OutlineNode.objects.create(
            course=self.course, title="Matter", order=0, depth=0
        )
        self.group = LearningObjectGroup.objects.create(outline_node=self.node)
        now = timezone.now()
        self.first = self._object("PDF one", now, SHORT)
        self.second = self._object("PDF two", now + timedelta(minutes=5), LONG)

    def _object(self, title, created_at, content):
        material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.node, title=title
        )
        LearningMaterial.objects.filter(pk=material.pk).update(created_at=created_at)
        material.refresh_from_db()
        return LearningObject.objects.create(
            material=material, group=self.group, title="Solid", content=content, order=0
        )

    @patch("course.variant_generator._request_variants")
    def test_partner_is_flagged_and_representative_is_not(self, request_variants):
        request_variants.return_value = {"SIMPLIFIED": "Solid keeps shape.", "ELABORATED": "x"}

        settle_group(self.group)

        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertIsNone(self.first.represented_by)
        self.assertEqual(self.second.represented_by, self.first)

    @patch("course.variant_generator._request_variants")
    def test_gap_is_filled_after_real_text_is_placed(self, request_variants):
        request_variants.return_value = {"SIMPLIFIED": "Solid keeps shape.", "ELABORATED": "ignored"}

        result = settle_group(self.group)

        self.assertEqual(result["generated"], ["SIMPLIFIED"])
        # Changed 2026-09-20: roles are per bundle; a PDF-supplied version is its own objects.
        self.assertEqual(result["bundle_roles"], {self.second.material_id: "ELABORATED"})
        simplified = LessonVariant.objects.get(learning_object=self.first, variant="SIMPLIFIED")
        self.assertEqual(simplified.origin, "generated")
        self.assertFalse(
            LessonVariant.objects.filter(origin=LessonVariant.Origin.SOURCE_PDF).exists()
        )

    @patch("course.variant_generator._request_variants")
    def test_llm_selects_original_and_only_missing_slot_is_generated(self, request_variants):
        self.classify_group_versions.side_effect = None
        self.classify_group_versions.return_value = {
            self.first.id: {"slot": "SIMPLIFIED", "confidence": 0.96, "reason": "Clearer."},
            self.second.id: {"slot": "ORIGINAL", "confidence": 0.94, "reason": "Balanced."},
        }
        request_variants.return_value = {
            "SIMPLIFIED": "ignored",
            "ELABORATED": "A fuller generated explanation.",
        }

        result = settle_group(self.group)

        self.assertEqual(result["representative_id"], self.second.id)
        self.assertEqual(result["generated"], ["ELABORATED"])
        # Changed 2026-09-20: roles are per bundle; a PDF-supplied version is its own objects.
        self.assertEqual(result["bundle_roles"], {self.first.material_id: "SIMPLIFIED"})
        elaborated = LessonVariant.objects.get(
            learning_object=self.second,
            variant="ELABORATED",
        )
        self.assertEqual(elaborated.origin, "generated")

    @patch("course.variant_generator._request_variants")
    def test_release_takes_back_its_own_text_and_drops_generated_rows(self, request_variants):
        request_variants.return_value = {"SIMPLIFIED": "Solid keeps shape.", "ELABORATED": "ignored"}
        settle_group(self.group)

        release_learning_object(self.second)

        self.second.refresh_from_db()
        self.assertIsNone(self.second.represented_by)
        # Changed 2026-09-20: roles are per bundle; a PDF-supplied version is its own objects.
        # The released object supplied the elaborated rung as its own text; no
        # copy of it was ever stored on the original.
        self.assertFalse(
            LessonVariant.objects.filter(origin=LessonVariant.Origin.SOURCE_PDF).exists()
        )
        # The generated rung existed only to complete a triple that no longer
        # has a partner, so it goes too.
        self.assertFalse(
            LessonVariant.objects.filter(learning_object=self.first, origin="generated").exists()
        )

    @patch("course.variant_generator._request_variants")
    def test_release_keeps_text_supplied_by_other_members(self, request_variants):
        request_variants.return_value = {"SIMPLIFIED": "Solid keeps shape.", "ELABORATED": "ignored"}
        third = self._object(
            "PDF three",
            timezone.now() + timedelta(minutes=9),
            "A solid keeps a fixed shape at all times. The particles inside it are packed "
            "together very closely. It cannot flow the way that water does.",
        )
        settle_group(self.group)
        # Changed 2026-09-20: roles are per bundle; a PDF-supplied version is its own objects.
        self.group.refresh_from_db()
        self.assertIn(third.material_id, bundle_roles(self.group))

        release_learning_object(self.second)

        # Another member's teacher-written text is untouched by this release.
        self.group.refresh_from_db()
        self.assertIn(third.material_id, bundle_roles(self.group))

    @patch("course.variant_generator._request_variants")
    def test_a_bundle_awaiting_confirmation_stays_its_own_teaching_step(self, request_variants):
        """Only a decided bundle is collapsed into the Normal one.

        Removing a bundle nobody has ruled on would drop content rather than
        deduplicate it, so it keeps its place in the lesson.
        """
        request_variants.return_value = {"SIMPLIFIED": "a", "ELABORATED": "b"}
        thin = self._object(
            "PDF three", timezone.now() + timedelta(minutes=9), MIDDLING
        )
        self.classify_group_versions.side_effect = None
        # Gemma ruled on the second PDF and said nothing about the third.
        self.classify_group_versions.return_value = {
            self.first.id: {"slot": "ORIGINAL", "confidence": 0.95, "reason": "Baseline."},
            self.second.id: {"slot": "ELABORATED", "confidence": 0.95, "reason": "Fuller."},
        }

        outcome = settle_group(self.group)

        thin.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual(
            [entry["material_id"] for entry in outcome["needs_confirmation"]],
            [thin.material_id],
        )
        self.assertIsNone(thin.represented_by)
        self.assertEqual(self.second.represented_by, self.first)

    @patch("course.variant_generator._request_variants")
    def test_llm_classified_member_is_represented_despite_thin_readability_margin(self, request_variants):
        request_variants.return_value = {"SIMPLIFIED": "a", "ELABORATED": "b"}
        LearningObject.objects.filter(pk=self.second.pk).update(group=None)
        middling = self._object(
            "PDF three", timezone.now() + timedelta(minutes=9), MIDDLING
        )

        settle_group(self.group)

        middling.refresh_from_db()
        self.assertEqual(middling.represented_by, self.first)


class ReleaseFromGroupTests(TestCase):
    """Leaving a group must not leave version links pointing across concepts."""

    def setUp(self):
        # Changed 2026-09-20: roles are per bundle; a PDF-supplied version is its own objects.
        # One object per PDF, so each member is a bundle of its own and a
        # departure really does take a role with it.
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.node = OutlineNode.objects.create(course=self.course, title="Matter", order=0, depth=0)
        self.group = LearningObjectGroup.objects.create(outline_node=self.node)
        self.original = LearningObject.objects.create(
            material=self._material("PDF one"), group=self.group,
            title="Solid examples", content=SHORT, order=0,
        )
        self.member = LearningObject.objects.create(
            material=self._material("PDF two"), group=self.group,
            title="Liquid examples", content=LONG, order=0,
            represented_by=self.original,
        )
        self.other = LearningObject.objects.create(
            material=self._material("PDF three"), group=self.group,
            title="Gas examples", content=MIDDLING, order=0,
            represented_by=self.original,
        )
        self.group.version_selection = {
            "normal_material_id": self.original.material_id,
            "bundle_roles": {
                str(self.member.material_id): "EXTRA",
                str(self.other.material_id): "ELABORATED",
            },
            "bundle_roles_assigned_by": {
                str(self.member.material_id): "teacher",
                str(self.other.material_id): "teacher",
            },
        }
        self.group.save(update_fields=["version_selection"])
        self.generated = LessonVariant.objects.create(
            learning_object=self.original, variant="SIMPLIFIED", narration="Short.",
            origin=LessonVariant.Origin.GENERATED, assigned_by=LessonVariant.AssignedBy.TEACHER,
        )

    def _material(self, title):
        return LearningMaterial.objects.create(
            course=self.course, outline_node=self.node, title=title,
        )

    def test_a_leaving_member_takes_back_only_its_own_text(self):
        # Changed 2026-09-20: roles are per bundle; a PDF-supplied version is its own objects.
        outcome = release_from_group(self.member, [self.original, self.other])

        self.member.refresh_from_db()
        self.group.refresh_from_db()
        self.assertIsNone(self.member.represented_by_id)
        # Its own bundle's role leaves with it; the other member's stays.
        self.assertEqual(bundle_roles(self.group), {self.other.material_id: "ELABORATED"})
        self.assertTrue(LessonVariant.objects.filter(pk=self.generated.pk).exists())
        self.assertEqual(outcome, {"was_original": False, "removed_version_slots": ["extra"]})

    def test_a_leaving_original_releases_everyone_it_represented(self):
        # Changed 2026-09-20: roles are per bundle; a PDF-supplied version is its own objects.
        outcome = release_from_group(self.original, [self.member, self.other])

        self.assertTrue(outcome["was_original"])
        self.assertEqual(outcome["removed_version_slots"], ["elaborated", "extra"])
        self.member.refresh_from_db()
        self.other.refresh_from_db()
        self.group.refresh_from_db()
        self.assertIsNone(self.member.represented_by_id)
        self.assertIsNone(self.other.represented_by_id)
        self.assertEqual(self.group.version_selection, {})
        # Generated text, including a teacher's edit, is never removed here.
        self.assertTrue(LessonVariant.objects.filter(pk=self.generated.pk).exists())

    def test_leaving_with_nobody_staying_changes_nothing(self):
        release_from_group(self.member, [])

        self.member.refresh_from_db()
        self.assertEqual(self.member.represented_by_id, self.original.id)


class CrossGroupRepairMigrationTests(TestCase):
    """The one-time repair for links left behind before the fix."""

    def test_links_and_texts_across_concepts_are_removed_and_nothing_else(self):
        from importlib import import_module

        from django.apps import apps

        course = CourseGroup.objects.create(title="Grade 1 Science")
        node = OutlineNode.objects.create(course=course, title="Matter", order=0, depth=0)
        solid_group = LearningObjectGroup.objects.create(outline_node=node)
        liquid_group = LearningObjectGroup.objects.create(outline_node=node)
        material = LearningMaterial.objects.create(course=course, outline_node=node, title="PDF")
        solid = LearningObject.objects.create(
            material=material, group=solid_group, title="Solid", content=SHORT, order=0,
        )
        partner = LearningObject.objects.create(
            material=material, group=solid_group, title="Solids", content=MIDDLING, order=1,
            represented_by=solid,
        )
        # Separated into another concept without its links being undone.
        liquid = LearningObject.objects.create(
            material=material, group=liquid_group, title="Liquid", content=LONG, order=2,
            represented_by=solid,
        )
        stale = LessonVariant.objects.create(
            learning_object=solid, variant="SIMPLIFIED", narration=LONG,
            origin=LessonVariant.Origin.SOURCE_PDF, source_learning_object=liquid,
        )
        valid = LessonVariant.objects.create(
            learning_object=solid, variant="ELABORATED", narration=MIDDLING,
            origin=LessonVariant.Origin.SOURCE_PDF, source_learning_object=partner,
        )

        migration = import_module("course.migrations.0007_repair_cross_group_version_links")
        migration.repair_cross_group_version_links(apps, None)

        liquid.refresh_from_db()
        partner.refresh_from_db()
        self.assertIsNone(liquid.represented_by_id)
        self.assertEqual(partner.represented_by_id, solid.id)
        self.assertFalse(LessonVariant.objects.filter(pk=stale.pk).exists())
        self.assertTrue(LessonVariant.objects.filter(pk=valid.pk).exists())


class BundleChunkTests(TestCase):
    """A chunk serves each version as the ordered objects it is taught as."""

    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="States")
        confirmed = {"learning_objects_confirmed": True}
        self.first = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="A", generated_json=dict(confirmed))
        self.second = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="B", generated_json=dict(confirmed))
        self.group = LearningObjectGroup.objects.create(outline_node=self.topic, label="Solid")
        self.normal = LearningObject.objects.create(
            material=self.first, group=self.group, title="Solid", order=0,
            content="A solid keeps its shape.",
        )
        self.normal_tail = LearningObject.objects.create(
            material=self.first, group=self.group, title="Particle diagram", order=1,
            section_title="Solid", content="Particles sit in a grid.",
        )
        self.simple = LearningObject.objects.create(
            material=self.second, group=self.group, title="Solids", order=0, section_title="Solids",
            content="Packed tight.",
        )
        self.simple_tail = LearningObject.objects.create(
            material=self.second, group=self.group, title="Examples", order=1,
            section_title="Solids", content="Ice cubes.",
        )
        assign_group_versions(self.group)
        # Readability cannot rank four-word samples, so the teacher's own
        # ruling stands in for it: the second PDF supplies SIMPLIFIED.
        set_bundle_role(self.group, self.second.id, "SIMPLIFIED")
        self.group.refresh_from_db()

    def test_a_versions_segments_follow_bundle_order(self):
        chunk = _build_chunk(self.normal)

        self.assertEqual(
            [segment["text"] for segment in chunk["variants"]["normal"]["segments"]],
            ["A solid keeps its shape.", "Particles sit in a grid."],
        )
        self.assertEqual(
            chunk["variants"]["normal"]["text"],
            "A solid keeps its shape.\nParticles sit in a grid.",
        )
        self.assertEqual(
            [segment["text"] for segment in chunk["variants"]["simplified"]["segments"]],
            ["Packed tight.", "Ice cubes."],
        )

    def test_a_generated_versions_segments_follow_the_normal_bundle(self):
        for item, text in (
            (self.normal, "Solids hold their shape at length."),
            (self.normal_tail, "The grid of particles barely moves."),
        ):
            LessonVariant.objects.create(
                learning_object=item, variant="ELABORATED", narration=text,
                origin=LessonVariant.Origin.GENERATED,
            )

        elaborated = _build_chunk(self.normal)["variants"]["elaborated"]

        self.assertEqual(
            [segment["text"] for segment in elaborated["segments"]],
            ["Solids hold their shape at length.", "The grid of particles barely moves."],
        )
        self.assertEqual(
            elaborated["text"],
            "Solids hold their shape at length.\nThe grid of particles barely moves.",
        )
        self.assertEqual(elaborated["origin"], LessonVariant.Origin.GENERATED)

    def test_a_generated_version_short_of_the_bundle_is_not_served(self):
        # One object's generation failed, so this Elaborated covers only half
        # the concept. Served, it would read as the whole lesson to a student
        # who cannot see the page; omitted, it is simply a version still
        # missing, which the publish gate and the review screen already show.
        LessonVariant.objects.create(
            learning_object=self.normal, variant="ELABORATED",
            narration="Solids hold their shape at length.",
            origin=LessonVariant.Origin.GENERATED,
        )

        chunk = _build_chunk(self.normal)

        self.assertNotIn("elaborated", chunk["variants"])
        self.assertFalse(chunk["versions_complete"])

    def test_the_package_serves_one_chunk_per_concept(self):
        module = CourseModule.objects.create(source=self.topic)
        node = LessonNode.objects.create(module=module, source=self.first)

        package = LessonPackageService.build_package(node.id)

        self.assertEqual([chunk["id"] for chunk in package["chunks"]], [self.normal.id])

    def test_a_versions_text_is_exactly_its_segments_joined(self):
        # Narration is TTS-adapted wording, so it differs from the source
        # text; a caption built from "text" must still match the segments.
        self.second.generated_json = {
            "learning_objects_confirmed": True,
            "lesson_audio_generated": True,
            "lesson_playlist": [
                {"learning_object_id": self.simple.id,
                 "narration": "The bits are packed really tight.",
                 "audio_url": "/media/simple-0.mp3"},
                {"learning_object_id": self.simple_tail.id,
                 "narration": "Think of an ice cube.",
                 "audio_url": "/media/simple-1.mp3"},
            ],
        }
        self.second.save(update_fields=["generated_json"])

        simplified = _build_chunk(self.normal)["variants"]["simplified"]

        self.assertEqual(
            [segment["text"] for segment in simplified["segments"]],
            ["The bits are packed really tight.", "Think of an ice cube."],
        )
        self.assertEqual(
            simplified["text"],
            "\n".join(segment["text"] for segment in simplified["segments"]),
        )
        self.assertEqual(simplified["audio_url"], "/media/simple-0.mp3")
