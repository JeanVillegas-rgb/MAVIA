"""A concept's questions come from the whole Normal bundle.

The Normal track speaks every object of that bundle, so a bank generated from
the lead object alone would ask about a third of what the student hears.
"""

from unittest.mock import patch

from django.test import TestCase

from lessons.models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
)
from question_generation.services.pipeline import (
    _is_concept_source,
    concept_source_text,
)


class BundleQuestionSourceTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="States")
        self.material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="A",
            generated_json={"learning_objects_confirmed": True})
        self.group = LearningObjectGroup.objects.create(outline_node=self.topic, label="Solid")
        self.lead = LearningObject.objects.create(
            material=self.material, group=self.group, title="Solid", order=0,
            content="A solid keeps its shape.")
        self.tail = LearningObject.objects.create(
            material=self.material, group=self.group, title="Everyday examples", order=1,
            section_title="Solid", content="Ice cubes and a rock.")

    def test_the_source_text_is_the_whole_normal_bundle(self):
        self.assertEqual(
            concept_source_text(self.lead),
            "A solid keeps its shape.\nIce cubes and a rock.",
        )

    def test_an_ungrouped_object_uses_its_own_text(self):
        loose = LearningObject.objects.create(
            material=self.material, group=None, title="Loose", order=2, content="On its own.")

        self.assertEqual(concept_source_text(loose), "On its own.")

    def test_the_bank_fingerprint_follows_the_bundle(self):
        from question_generation.services.pipeline import question_bank_fingerprint, QUESTION_DISTRIBUTION

        before = question_bank_fingerprint(concept_source_text(self.lead), QUESTION_DISTRIBUTION)
        self.tail.content = "Ice cubes, a rock and a coin."
        self.tail.save(update_fields=["content"])

        after = question_bank_fingerprint(concept_source_text(self.lead), QUESTION_DISTRIBUTION)
        self.assertNotEqual(before, after)

    def test_every_telling_of_the_concept_reaches_the_prompt(self):
        """A second PDF's telling is text the learner hears on remediation,
        so questions must be written from it too."""
        other = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="B",
            generated_json={"learning_objects_confirmed": True})
        member = LearningObject.objects.create(
            material=other, group=self.group, title="Solids", order=0,
            represented_by=self.lead,
            content="Solid particles vibrate in place.")
        self.group.version_selection = {
            "normal_material_id": self.material.id,
            "bundle_roles": {str(member.material_id): "ELABORATED"},
            "bundle_roles_assigned_by": {str(member.material_id): "teacher"},
        }
        self.group.save(update_fields=["version_selection"])
        text = concept_source_text(self.lead)
        self.assertIn("A solid keeps its shape.", text)
        self.assertIn("Ice cubes and a rock.", text)
        self.assertIn("Solid particles vibrate in place.", text)

    def test_a_single_telling_concept_is_unchanged(self):
        """10 of topic 276's 19 concepts come from one PDF. They must read
        exactly as before, with no blank lines and nothing duplicated."""
        self.assertEqual(
            concept_source_text(self.lead),
            "A solid keeps its shape.\nIce cubes and a rock.",
        )

    def test_a_blank_sibling_does_not_become_a_second_concept_source(self):
        """A whitespace-only object is not a telling of anything.

        version_bundles() dropped blank-content objects (_eligible_bundles);
        bundles_for_group() does not. Without the same filter, such an object
        passes the membership test, reads the whole concept as its own source
        and generates a second, identical bank -- double the LLM and gate
        spend, invisible to the per-node dedup.
        """
        blank = LearningObject.objects.create(
            material=self.material, group=self.group, title="Blank",
            order=5, content="   ")
        self.assertEqual(concept_source_text(blank), "   ")

    def test_an_unassigned_bundle_does_not_feed_question_generation(self):
        other = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="B",
            generated_json={"learning_objects_confirmed": True})
        LearningObject.objects.create(
            material=other, group=self.group, title="Solids", order=0,
            represented_by=self.lead, content="Solid particles vibrate.")
        self.assertNotIn("Solid particles vibrate.", concept_source_text(self.lead))

    def test_an_unassigned_bundle_is_not_offered_to_the_generator(self):
        extra = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="C",
            generated_json={"learning_objects_confirmed": True})
        LearningObject.objects.create(
            material=extra, group=self.group, title="Aside", order=0,
            represented_by=self.lead, content="An unrelated aside.")
        self.group.version_selection = {
            "normal_material_id": self.material.id,
            "bundle_roles": {},
        }
        self.group.save(update_fields=["version_selection"])
        self.assertNotIn("An unrelated aside.", concept_source_text(self.lead))


class BundleGenerationScopeTests(TestCase):
    """One concept generates one bank, from its Normal bundle's lead.

    Every object of the bundle reads the same source text, so generating per
    object would write the same bank two or three times and charge the teacher
    for each.
    """

    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="States")
        self.material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="A",
            generated_json={"learning_objects_confirmed": True})
        self.group = LearningObjectGroup.objects.create(outline_node=self.topic, label="Solid")
        self.lead = LearningObject.objects.create(
            material=self.material, group=self.group, title="Solid", order=0,
            content="A solid keeps its shape.")
        self.tail = LearningObject.objects.create(
            material=self.material, group=self.group, title="Everyday examples", order=1,
            section_title="Solid", content="Ice cubes and a rock.")

    def _run(self, **kwargs):
        from question_generation.services import pipeline

        with patch.object(pipeline, "_get_classifier", return_value=object()), patch.object(
            pipeline, "generate_questions_for_node", return_value=[],
        ) as generate_node:
            pipeline.generate_questions_for_material(self.material, **kwargs)
        return generate_node

    def test_a_two_object_bundle_generates_once_from_its_lead(self):
        generate_node = self._run()

        generate_node.assert_called_once()
        self.assertEqual(generate_node.call_args.args[0].id, self.lead.id)

    def test_asking_for_a_non_lead_object_generates_nothing_and_says_so(self):
        """Today's behaviour, pinned rather than endorsed: a bundle member that
        is not the lead has no bank of its own, so an explicit request for it
        does nothing. It is now logged instead of passing in silence."""
        from question_generation.services import pipeline

        with self.assertLogs(pipeline.logger, level="INFO") as logs:
            generate_node = self._run(node_ids=[self.tail.id])

        generate_node.assert_not_called()
        self.assertEqual(self.tail.generated_questions.count(), 0)
        self.assertTrue(
            any("Everyday examples" in line for line in logs.output),
            logs.output,
        )


class WastedGenerationTests(TestCase):
    """A bank that finalize_node_questions will delete is not worth generating.

    Measured on topic 276: 42 objects generated a bank and 22 of them were
    deleted moments later, because a concept owns exactly one bank and it
    belongs to the Normal bundle's lead. That was 52% of a 117-minute run.
    """

    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="States")
        self.normal = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="A",
            generated_json={"learning_objects_confirmed": True})
        self.other = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="B",
            generated_json={"learning_objects_confirmed": True})
        self.group = LearningObjectGroup.objects.create(
            outline_node=self.topic, label="Solid",
            version_selection={"normal_material_id": self.normal.id})
        self.lead = LearningObject.objects.create(
            material=self.normal, group=self.group, title="Solid", order=0,
            content="A solid keeps its shape.")
        self.telling = LearningObject.objects.create(
            material=self.other, group=self.group, title="Solids", order=0,
            represented_by=self.lead, content="Solid particles vibrate.")

    def test_the_normal_bundles_lead_generates(self):
        self.assertTrue(_is_concept_source(self.lead))

    def test_another_pdfs_telling_does_not_generate(self):
        """Its bank is deleted by finalize_node_questions the moment the
        lead finalizes, so making it is pure cost."""
        self.assertFalse(_is_concept_source(self.telling))

    def test_a_concept_with_no_normal_bundle_still_generates(self):
        """Nothing owns the concept, so refusing every member would leave it
        with no questions at all."""
        orphan_group = LearningObjectGroup.objects.create(
            outline_node=self.topic, label="Orphan",
            version_selection={"normal_material_id": 999999})
        orphan = LearningObject.objects.create(
            material=self.other, group=orphan_group, title="Orphan", order=9,
            content="Text with no Normal bundle.")
        self.assertTrue(_is_concept_source(orphan))

    def test_an_ungrouped_object_still_generates(self):
        loose = LearningObject.objects.create(
            material=self.normal, group=None, title="Loose", order=5,
            content="On its own.")
        self.assertTrue(_is_concept_source(loose))
