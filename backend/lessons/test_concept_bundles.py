"""Bundles: a concept's objects from one PDF, in document order.

A bundle is derived rather than stored, so every consumer must obtain it the
same way; these tests pin the ordering and the naming rules the rest of the
pipeline depends on.
"""

from django.test import TestCase

from .models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
)
from .services.concept_bundles import (
    bundle_heading,
    bundle_label,
    bundle_lead,
    bundle_text,
    bundles_for_group,
    material_order,
    ordered_members,
)


class BundleFixture(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="States")
        self.first = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="First",
            generated_json={"learning_objects_confirmed": True},
        )
        self.second = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="Second",
            generated_json={"learning_objects_confirmed": True},
        )
        self.group = LearningObjectGroup.objects.create(outline_node=self.topic, label="Solid")

    def _object(self, material, title, order, section="", content=None):
        return LearningObject.objects.create(
            material=material, group=self.group, title=title, order=order,
            section_title=section, content=content if content is not None else f"{title} text.",
        )


class BundleTests(BundleFixture):
    def test_objects_are_grouped_by_pdf_in_document_order(self):
        examples = self._object(self.second, "Everyday examples", 3, "Solids")
        solids = self._object(self.second, "Solids", 1, "Solids")
        diagram = self._object(self.second, "Diagram description", 2, "Solids")
        solid = self._object(self.first, "Solid", 5)

        bundles = bundles_for_group(self.group)

        self.assertEqual(bundles[self.first.id], [solid])
        self.assertEqual(bundles[self.second.id], [solids, diagram, examples])

    def test_members_are_ordered_by_upload_then_document_order(self):
        second_object = self._object(self.second, "Solids", 1, "Solids")
        first_object = self._object(self.first, "Solid", 5)

        self.assertEqual(ordered_members(self.group), [first_object, second_object])
        self.assertEqual(material_order(self.topic), [self.first.id, self.second.id])

    def test_a_prefetched_concept_is_read_without_another_query(self):
        """Finding 5: `bundles_for_group` asked for `select_related("material")`
        unconditionally, which issued a fresh query per concept and undid the
        caller's prefetch; `ordered_members` then spent two more deriving the
        topic's upload order for every concept."""
        self._object(self.second, "Solids", 1, "Solids")
        self._object(self.first, "Solid", 5)
        ranked = {material_id: rank for rank, material_id in enumerate(material_order(self.topic))}
        group = (
            LearningObjectGroup.objects
            .prefetch_related("learning_objects__material")
            .get(pk=self.group.pk)
        )

        with self.assertNumQueries(0):
            members = ordered_members(group, ranked=ranked)
            [item.material.title for item in members]

        self.assertEqual([item.title for item in members], ["Solid", "Solids"])

    def test_bundle_text_joins_content_and_skips_empties(self):
        first = self._object(self.second, "Solids", 1, "Solids", content="Packed tightly.")
        empty = self._object(self.second, "Figure", 2, "Solids", content="   ")
        last = self._object(self.second, "Everyday examples", 3, "Solids", content="Ice cubes.")

        self.assertEqual(bundle_text([first, empty, last]), "Packed tightly.\nIce cubes.")

    def test_lead_and_heading_come_from_the_bundle_not_the_first_title(self):
        figure = self._object(self.first, "Okay, let us describe this figure", 0)
        matter = self._object(self.first, "Matter", 1, "Matter")

        self.assertEqual(bundle_lead([figure, matter]), figure)
        self.assertEqual(bundle_heading([figure, matter]), "Matter")

    def test_heading_falls_back_to_the_first_title(self):
        only = self._object(self.first, "Changing From One State to Another", 0)

        self.assertEqual(bundle_heading([only]), "Changing From One State to Another")


class BundleLabelTests(BundleFixture):
    """Design 3.5: the heading names a concept only for a bundle of 2+."""

    def test_a_lone_object_under_a_shared_heading_keeps_its_own_title(self):
        # Solid, Liquid and Gas all sit under "Matter" in the first PDF.
        solid = self._object(self.first, "Solid", 1, "Matter")
        liquid = self._object(self.first, "Liquid", 2, "Matter")
        gas = self._object(self.first, "Gas", 3, "Matter")

        self.assertEqual(bundle_label([solid]), "Solid")
        self.assertEqual(bundle_label([liquid]), "Liquid")
        self.assertEqual(bundle_label([gas]), "Gas")

    def test_a_multi_object_bundle_still_takes_the_heading(self):
        shape = self._object(self.second, "Shape", 1, "Comparing the Three States")
        volume = self._object(self.second, "Volume", 2, "Comparing the Three States")

        self.assertEqual(bundle_label([shape, volume]), "Comparing the Three States")
        # Even when the bundle opens with a figure that names nothing.
        figure = self._object(self.first, "Figure", 0)
        matter = self._object(self.first, "Matter", 1, "Matter")
        self.assertEqual(bundle_label([figure, matter]), "Matter")

    def test_an_empty_bundle_names_nothing(self):
        self.assertEqual(bundle_label([]), "")
