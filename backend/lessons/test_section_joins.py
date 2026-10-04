"""Section joining is decided across the topic, not at the first upload.

Uploaded first, PDF 1's "Solid", "Liquid" and "Gas" joined its "Matter"
section because no other PDF existed yet to teach them. PDF 2's and PDF 3's
sections on each state then matched that swollen concept, and the topic lost
its three states (BUG-001 A). Measured on the three Solid, Liquid and Gas
PDFs after the fix: upload orders 1-2-3, 3-2-1 and 2-3-1 give the same 11
concepts.
"""

from django.test import TestCase

from .models import CourseGroup, LearningMaterial, LearningObject, LearningObjectGroup, OutlineNode
from .services.learning_resource_linker import (
    SECTION_JOINS_KEY,
    attach_orphan_objects_to_their_section,
    join_sections_across_topic,
    release_section_joins,
)


class SectionJoinTests(TestCase):
    def setUp(self):
        course = CourseGroup.objects.create(title="Science")
        self.node = OutlineNode.objects.create(course=course, title="States of matter")
        self.pdf1 = self.material(course, "PDF 1")
        self.pdf2 = self.material(course, "PDF 2")

    def material(self, course, title):
        return LearningMaterial.objects.create(
            course=course, outline_node=self.node, title=title,
            generated_json={"learning_objects_confirmed": True},
        )

    def add(self, material, title, section="", order=0, group=None):
        return LearningObject.objects.create(
            material=material, title=title, section_title=section, content=f"{title} text.",
            order=order,
            group=group or LearningObjectGroup.objects.create(outline_node=self.node, label=title),
        )

    def test_a_join_made_before_other_pdfs_exist_is_released_and_redone(self):
        matter = self.add(self.pdf1, "Matter", "Matter", 0)
        solid = self.add(self.pdf1, "Solid", "Matter", 1)

        # PDF 1 alone: nothing else teaches "Solid", so it joins "Matter".
        attach_orphan_objects_to_their_section(self.pdf1)
        solid.refresh_from_db()
        self.assertEqual(solid.group_id, matter.group_id)

        # PDF 2 arrives: the join is released before PDF 2 is matched ...
        released = release_section_joins(self.pdf2)
        solid.refresh_from_db()
        self.assertEqual(released, [solid.id])
        self.assertNotEqual(solid.group_id, matter.group_id)

        # ... PDF 2's section on solids matches PDF 1's "Solid" ...
        self.add(self.pdf2, "SOLID", "SOLID", 0, group=solid.group)

        # ... so redone across the topic, "Solid" stays a concept of its own.
        join_sections_across_topic(self.pdf2)
        solid.refresh_from_db()
        self.assertNotEqual(solid.group_id, matter.group_id)

    def test_a_part_nothing_else_teaches_rejoins_its_section(self):
        matter = self.add(self.pdf1, "Matter", "Matter", 0)
        solid = self.add(self.pdf1, "Solid", "Matter", 1)
        attach_orphan_objects_to_their_section(self.pdf1)

        release_section_joins(self.pdf2)
        join_sections_across_topic(self.pdf2)

        solid.refresh_from_db()
        self.assertEqual(solid.group_id, matter.group_id)

    def test_a_part_a_teacher_moved_is_never_released(self):
        matter = self.add(self.pdf1, "Matter", "Matter", 0)
        solid = self.add(self.pdf1, "Solid", "Matter", 1)
        attach_orphan_objects_to_their_section(self.pdf1)
        teacher_group = LearningObjectGroup.objects.create(outline_node=self.node, label="States")
        solid.group = teacher_group
        solid.save(update_fields=["group"])

        released = release_section_joins(self.pdf2)

        solid.refresh_from_db()
        self.assertEqual(released, [])
        self.assertEqual(solid.group_id, teacher_group.id)
        self.pdf1.refresh_from_db()
        self.assertNotIn(SECTION_JOINS_KEY, self.pdf1.generated_json)

    def test_a_later_piece_follows_its_first_piece(self):
        """"What Is Matter? (Part 2 of 2)" was split from its Part 1."""
        matter_group = LearningObjectGroup.objects.create(outline_node=self.node, label="Matter")
        self.add(self.pdf1, "Matter", "", 0, group=matter_group)
        self.add(self.pdf2, "What Is Matter? (Part 1 of 2)", "", 0, group=matter_group)
        part2 = self.add(self.pdf2, "What Is Matter? (Part 2 of 2)", "", 1)

        attach_orphan_objects_to_their_section(self.pdf2)

        part2.refresh_from_db()
        self.assertEqual(part2.group_id, matter_group.id)

    def test_pieces_of_different_passages_sharing_a_title_are_never_fused(self):
        solid_part1 = self.add(self.pdf1, "Diagram description (Part 1 of 2)", "Solids", 0)
        self.add(self.pdf1, "Diagram description (Part 2 of 2)", "Solids", 1, group=solid_part1.group)
        gas_part1 = self.add(self.pdf1, "Diagram description (Part 1 of 2)", "Gases", 2)
        gas_part2 = self.add(self.pdf1, "Diagram description (Part 2 of 2)", "Gases", 3)

        attach_orphan_objects_to_their_section(self.pdf1)

        gas_part2.refresh_from_db()
        self.assertEqual(gas_part2.group_id, gas_part1.group_id)
        self.assertNotEqual(gas_part2.group_id, solid_part1.group_id)

    def test_glossary_terms_stay_concepts_of_their_own(self):
        plot = self.add(self.pdf1, "Plot", "Key Vocabulary", 0)
        climax = self.add(self.pdf1, "Climax", "Key Vocabulary", 1)

        attach_orphan_objects_to_their_section(self.pdf1)

        plot.refresh_from_db()
        climax.refresh_from_db()
        self.assertNotEqual(plot.group_id, climax.group_id)

    def test_a_concept_standing_for_a_heading_with_no_text_is_named_after_it(self):
        """PDF 1 prints "Comparing the Three States" over Shape, Volume and Flow;
        the concept was called "Shape"."""
        shape = self.add(self.pdf1, "Shape", "Comparing the Three States", 0)
        self.add(self.pdf1, "Volume", "Comparing the Three States", 1)

        attach_orphan_objects_to_their_section(self.pdf1)

        shape.group.refresh_from_db()
        self.assertEqual(shape.group.label, "Comparing the Three States")

    def test_a_teacher_named_concept_keeps_its_name(self):
        shape = self.add(self.pdf1, "Shape", "Comparing the Three States", 0)
        shape.group.label = "Properties of the states"
        shape.group.version_selection = {"label_locked": True}
        shape.group.save(update_fields=["label", "version_selection"])
        self.add(self.pdf1, "Volume", "Comparing the Three States", 1)

        attach_orphan_objects_to_their_section(self.pdf1)

        shape.group.refresh_from_db()
        self.assertEqual(shape.group.label, "Properties of the states")
