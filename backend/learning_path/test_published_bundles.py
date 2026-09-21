"""The published path must serve a concept's whole bundle, not just its lead.

Found on the real publish of topic 152 (2026-09-21). The concept "Comparing
the Three States" holds a Normal bundle of four objects and a Simplified
supplied by the other PDF's two objects. ``get_published_path`` served 77
characters -- the lead object alone -- and a 66-character *generated*
Simplified, while the lesson package served 326 and 698. Across the two live
topics, 15 of 22 concepts were affected.

Two causes, both in ``_versions``:

* Normal was read as ``representative.content``, so every object after the
  bundle lead was dropped.
* Simplified and Elaborated were read from ``LessonVariant`` rows only, and a
  version a PDF *supplies* has no such row by design -- its text is its
  objects. So the other PDF's wording never reached the path at all.

This is the third consumer of that design to be caught reading rows instead of
asking for bundles; the publish gate and audio generation were the first two.
"""

from django.test import TestCase
from django.utils import timezone

from course.models import LessonVariant
from course.version_assignment import set_bundle_role
from lessons.models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
)

from .models import LearningPathStep
from .services.published import get_published_path


class PublishedBundleTests(TestCase):
    """One concept taught as four objects in PDF A and two in PDF B."""

    def setUp(self):
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.topic = OutlineNode.objects.create(
            course=self.course, title="Solid, Liquid and Gas", order=0, depth=0,
        )
        confirmed = {"learning_objects_confirmed": True}
        self.first = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="PDF A",
            generated_json=dict(confirmed),
        )
        self.second = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="PDF B",
            generated_json=dict(confirmed),
        )
        self.group = LearningObjectGroup.objects.create(
            outline_node=self.topic, label="Comparing the Three States",
        )

        # PDF A teaches the comparison as four short attribute passages.
        self.lead = self._object(self.first, "Shape", "Solids keep their shape.", 0)
        self.volume = self._object(self.first, "Volume", "Gases expand to fill space.", 1)
        self.spacing = self._object(self.first, "Particle arrangement", "Gas particles are widely spaced.", 2)
        self.flow = self._object(self.first, "Flow", "Liquids and gases can flow.", 3)

        # PDF B teaches it as a caption and a table.
        self.caption = self._object(self.second, "Comparing the Three States", "The table below summarizes the differences.", 0)
        self.table = self._object(self.second, "5. Comparing the Three States", "Solids hold shape; liquids flow; gases fill the space.", 1)
        for item in (self.caption, self.table):
            item.represented_by = self.lead
            item.save(update_fields=["represented_by"])

        LearningPathStep.objects.create(
            outline_node=self.topic, concept=self.group, position=1, depth=0,
            published_at=timezone.now(),
        )

    def _object(self, material, title, content, order):
        return LearningObject.objects.create(
            material=material, group=self.group, title=title, content=content,
            order=order, section_title="Comparing the Three States",
        )

    def _step(self):
        return get_published_path(self.topic)["steps"][0]

    # ── Normal ────────────────────────────────────────────────────────────

    def test_normal_is_the_whole_bundle_not_only_its_lead(self):
        normal = self._step()["versions"]["normal"]["text"]

        for item in (self.lead, self.volume, self.spacing, self.flow):
            self.assertIn(item.content, normal)

    def test_normal_keeps_the_bundle_in_document_order(self):
        normal = self._step()["versions"]["normal"]["text"]

        positions = [normal.index(item.content) for item in
                     (self.lead, self.volume, self.spacing, self.flow)]
        self.assertEqual(positions, sorted(positions))

    def test_a_concept_taught_by_one_object_is_unchanged(self):
        solo_group = LearningObjectGroup.objects.create(outline_node=self.topic, label="Flowing")
        solo = LearningObject.objects.create(
            material=self.first, group=solo_group, title="Flowing",
            content="Only this passage teaches it.", order=9,
        )
        LearningPathStep.objects.create(
            outline_node=self.topic, concept=solo_group, position=2, depth=0,
            published_at=timezone.now(),
        )

        step = [s for s in get_published_path(self.topic)["steps"] if s["concept_id"] == solo_group.id][0]

        self.assertEqual(step["versions"]["normal"]["text"], solo.content)

    # ── Versions a PDF supplies ───────────────────────────────────────────

    def test_a_version_supplied_by_a_pdf_reaches_the_path(self):
        set_bundle_role(self.group, self.second.id, "SIMPLIFIED")

        simplified = self._step()["versions"]["simplified"]

        self.assertIsNotNone(simplified, "the other PDF's bundle was dropped entirely")
        self.assertIn(self.caption.content, simplified["text"])
        self.assertIn(self.table.content, simplified["text"])

    def test_a_supplied_version_outranks_a_stale_generated_one(self):
        """A generated row can survive a regrouping; the PDF's wording wins.

        On the live topic the lead still carried a Simplified generated while
        it was a concept of its own, and the path served that instead of the
        bundle a teacher had just connected to it.
        """
        set_bundle_role(self.group, self.second.id, "SIMPLIFIED")
        LessonVariant.objects.create(
            learning_object=self.lead, variant="SIMPLIFIED",
            narration="Stale wording from before the concepts were joined.",
            origin=LessonVariant.Origin.GENERATED,
        )

        simplified = self._step()["versions"]["simplified"]["text"]

        self.assertNotIn("Stale wording", simplified)
        self.assertIn(self.table.content, simplified)

    def test_a_generated_version_is_still_served_when_no_pdf_supplies_one(self):
        for item in (self.lead, self.volume, self.spacing, self.flow):
            LessonVariant.objects.create(
                learning_object=item, variant="ELABORATED",
                narration=f"Elaborated {item.title}.",
                origin=LessonVariant.Origin.GENERATED,
            )

        elaborated = self._step()["versions"]["elaborated"]

        self.assertIsNotNone(elaborated)
        for item in (self.lead, self.volume, self.spacing, self.flow):
            self.assertIn(f"Elaborated {item.title}.", elaborated["text"])

    def test_a_role_nobody_supplies_or_generated_stays_absent(self):
        self.assertIsNone(self._step()["versions"]["elaborated"])

    def test_a_generated_version_short_of_the_bundle_is_not_half_served(self):
        """Half a track reads as a whole lesson to someone who cannot see it."""
        LessonVariant.objects.create(
            learning_object=self.lead, variant="ELABORATED",
            narration="Only the first object was written.",
            origin=LessonVariant.Origin.GENERATED,
        )

        self.assertIsNone(self._step()["versions"]["elaborated"])

    # ── Shape ─────────────────────────────────────────────────────────────

    def test_the_documented_keys_still_exist(self):
        versions = self._step()["versions"]

        self.assertEqual(set(versions), {"normal", "simplified", "elaborated"})
        self.assertLessEqual({"text", "audio_url"}, set(versions["normal"]))

    def test_every_object_of_a_version_is_reachable_as_its_own_segment(self):
        """A single audio_url is only the first clip of a four-object version.

        Without the segments a reader would play one quarter of the version and
        have no way to know the rest existed -- which for a learner who cannot
        see the page is the version simply being wrong.
        """
        segments = self._step()["versions"]["normal"]["segments"]

        self.assertEqual(
            [segment["text"] for segment in segments],
            [self.lead.content, self.volume.content, self.spacing.content, self.flow.content],
        )
