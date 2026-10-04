"""An object with no corroboration elsewhere is a part, not a concept.

PDF 49 writes "Melting -- solid to liquid, caused by adding heat." as its own
chunk under the heading "How Matter Changes State". No other PDF has it as a
separate object, so grouping leaves it alone and the learning path teaches it as
a standalone concept built from 39 characters.

The discriminator is corroboration, not the section: "Solid", "Liquid" and
"Gas" also sit under a heading ("Matter") and must stay three concepts, because
other PDFs teach them too.
"""

from django.test import TestCase

from .models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
)
from .services.learning_resource_linker import attach_orphan_objects_to_their_section


class OrphanSectionTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science 4")
        self.node = OutlineNode.objects.create(course=self.course, title="States of Matter")
        self.material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.node, title="Lesson PDF",
        )

    def add(self, title, section_title, order, group):
        return LearningObject.objects.create(
            material=self.material, title=title, section_title=section_title,
            content=f"{title} body text for this object.", order=order, group=group,
        )

    def group(self, label):
        return LearningObjectGroup.objects.create(outline_node=self.node, label=label)

    def test_an_orphan_joins_the_group_of_its_section_head(self):
        head_group, orphan_group = self.group("How Matter Changes State"), self.group("Melting")
        head = self.add("How Matter Changes State", "How Matter Changes State", 0, head_group)
        orphan = self.add("Melting", "How Matter Changes State", 1, orphan_group)

        attach_orphan_objects_to_their_section(self.material)

        orphan.refresh_from_db()
        self.assertEqual(orphan.group_id, head.group_id)

    def test_an_orphan_joins_a_head_that_does_not_name_itself_as_a_section(self):
        head_group, figure_group = self.group("Solids"), self.group("Particle diagram")
        head = self.add("Solids", "", 0, head_group)
        figure = self.add("Particle diagram", "Solids", 1, figure_group)

        attach_orphan_objects_to_their_section(self.material)

        figure.refresh_from_db()
        self.assertEqual(figure.group_id, head.group_id)

    def test_an_object_corroborated_elsewhere_is_never_moved(self):
        # "Solid" sits under a "Matter" heading but another PDF teaches it too,
        # so its group has a companion. It is a concept, not a part of Matter.
        head_group, solid_group = self.group("Matter"), self.group("Solid")
        head = self.add("Matter", "Matter", 0, head_group)
        solid = self.add("Solid", "Matter", 1, solid_group)
        other = LearningMaterial.objects.create(
            course=self.course, outline_node=self.node, title="Another PDF",
        )
        LearningObject.objects.create(
            material=other, title="Solids", section_title="", order=0,
            content="In a solid, particles are packed tightly together.",
            group=solid_group,
        )

        attach_orphan_objects_to_their_section(self.material)

        solid.refresh_from_db()
        self.assertEqual(solid.group_id, solid_group.id)
        self.assertNotEqual(solid.group_id, head.group_id)

    def test_an_object_whose_own_title_is_a_numbered_heading_is_not_moved(self):
        # Its section_title is the PREVIOUS section, mis-assigned by extraction.
        # It is a head whose section failed to register, not a part.
        head_group, own_group = self.group("Comparing the Three States"), self.group("changes")
        self.add("Comparing the Three States", "Comparing the Three States", 0, head_group)
        table = self.add("6. Changing From One State to Another",
                         "Comparing the Three States", 1, own_group)

        attach_orphan_objects_to_their_section(self.material)

        table.refresh_from_db()
        self.assertEqual(table.group_id, own_group.id)

    def test_an_object_with_no_section_title_is_not_moved(self):
        head_group, own_group = self.group("Solids"), self.group("Key idea")
        self.add("Solids", "Solids", 0, head_group)
        callout = self.add("Key idea", "", 1, own_group)

        attach_orphan_objects_to_their_section(self.material)

        callout.refresh_from_db()
        self.assertEqual(callout.group_id, own_group.id)

    def test_an_object_already_with_its_section_head_is_left_alone(self):
        shared = self.group("Solids")
        self.add("Solids", "Solids", 0, shared)
        part = self.add("Everyday examples", "Solids", 1, shared)

        moved = attach_orphan_objects_to_their_section(self.material)

        part.refresh_from_db()
        self.assertEqual(part.group_id, shared.id)
        self.assertEqual(moved, [])

    def test_a_material_with_no_section_titles_is_unchanged(self):
        a, b = self.group("One"), self.group("Two")
        first = self.add("One", "", 0, a)
        second = self.add("Two", "", 1, b)

        moved = attach_orphan_objects_to_their_section(self.material)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.group_id, second.group_id), (a.id, b.id))
        self.assertEqual(moved, [])
