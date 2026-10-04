"""``repair_bundle_labels``: re-derive the labels the old rule already wrote.

The pipeline fix (design §3.5) only governs labels written from now on. This
command repairs the rows an existing course already carries, and must be at
least as careful as the pipeline about whose wording it is touching.
"""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from .models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
)


class RepairBundleLabelsTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(
            course=self.course, title="Solid, Liquid and Gas", published=True,
        )
        self.material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="Lesson one",
            generated_json={"learning_objects_confirmed": True},
        )

    def _concept(self, label, selection=None, **object_kwargs):
        group = LearningObjectGroup.objects.create(
            outline_node=self.topic, label=label,
            version_selection=selection if selection is not None else {"auto_label": label},
        )
        LearningObject.objects.create(
            material=self.material, group=group, order=0,
            content=object_kwargs.pop("content", "Some lesson text."),
            **object_kwargs,
        )
        return group

    def _run(self, *args):
        out = StringIO()
        call_command("repair_bundle_labels", *args, stdout=out)
        return out.getvalue()

    def test_a_lone_object_is_renamed_from_the_heading_to_its_own_title(self):
        group = self._concept("Matter", title="Solid", section_title="Matter")

        output = self._run()

        group.refresh_from_db()
        self.assertEqual(group.label, "Solid")
        self.assertEqual(group.version_selection["auto_label"], "Solid")
        self.assertIn("'Matter' -> 'Solid'", output)

    def test_a_bundle_of_two_keeps_its_heading(self):
        group = self._concept(
            "Comparing the Three States", title="Shape",
            section_title="Comparing the Three States",
        )
        LearningObject.objects.create(
            material=self.material, group=group, order=1, title="Volume",
            section_title="Comparing the Three States", content="More lesson text.",
        )

        self._run()

        group.refresh_from_db()
        self.assertEqual(group.label, "Comparing the Three States")

    def test_a_locked_label_is_reported_and_left_alone(self):
        group = self._concept(
            "Teacher's concept name",
            selection={"auto_label": "Teacher's concept name", "label_locked": True},
            title="Solid", section_title="Matter",
        )

        output = self._run()

        group.refresh_from_db()
        self.assertEqual(group.label, "Teacher's concept name")
        self.assertIn("[locked]", output)

    def test_a_label_this_pipeline_never_wrote_is_reported_and_left_alone(self):
        group = self._concept(
            "States of Matter", selection={}, title="Solid", section_title="Matter",
        )

        output = self._run()

        group.refresh_from_db()
        self.assertEqual(group.label, "States of Matter")
        self.assertIn("[teacher]", output)

    def test_a_concept_with_no_eligible_bundle_is_skipped_not_guessed(self):
        """No Normal bundle means no answer -- never a fallback to every PDF.

        Falling back to all of the concept's members at once hands a
        multi-object list to the heading rule, which is the very thing this
        command exists to undo.
        """
        group = self._concept(
            "Matter", title="Solid", section_title="Matter", content="   ",
        )
        second = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="Lesson two",
            generated_json={"learning_objects_confirmed": True},
        )
        LearningObject.objects.create(
            material=second, group=group, order=0, title="Solids",
            section_title="Matter", content="  ",
        )

        output = self._run()

        group.refresh_from_db()
        self.assertEqual(group.label, "Matter")
        self.assertIn("[no bundle]", output)

    def test_a_lone_figure_titled_with_model_chatter_keeps_its_own_title(self):
        """Live concept #272's exact shape.

        Its object's title is the opening line of an old figure description --
        "Okay, let's describe this figure for the student". The label rule is
        right and must not reach for the heading instead; the input is what is
        bad, and regenerating the description is what fixes it.
        """
        chatter = "Okay, let's describe this figure for the student"
        group = self._concept(
            "Matter", selection={"auto_label": "Matter"},
            title=chatter, section_title="Matter",
            kind=LearningObject.Kind.IMAGE,
        )

        self._run()

        group.refresh_from_db()
        self.assertEqual(group.label, chatter)

    def test_a_topic_whose_labels_changed_is_unpublished(self):
        # A concept's name feeds the criteria's same-name veto, so the
        # published path was derived from the old colliding names.
        self._concept("Matter", title="Solid", section_title="Matter")

        output = self._run()

        self.topic.refresh_from_db()
        self.assertFalse(self.topic.published)
        self.assertIsNone(self.topic.published_at)
        self.assertIn(f"topic {self.topic.id} unpublished", output)

    def test_a_topic_whose_labels_are_already_right_stays_published(self):
        self._concept("Solid", title="Solid", section_title="Matter")

        self._run()

        self.topic.refresh_from_db()
        self.assertTrue(self.topic.published)

    def test_a_dry_run_writes_nothing_and_leaves_the_topic_published(self):
        group = self._concept("Matter", title="Solid", section_title="Matter")

        output = self._run("--dry-run")

        group.refresh_from_db()
        self.topic.refresh_from_db()
        self.assertEqual(group.label, "Matter")
        self.assertTrue(self.topic.published)
        self.assertIn("would be corrected", output)
        self.assertIn("Dry run: nothing was written.", output)
