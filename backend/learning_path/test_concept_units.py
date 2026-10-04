"""A concept is one teaching unit of a topic, assembled from several PDFs.

These pin the two things that make a topic-level path possible at all: that a
concept keeps every member across files, and that its position is the earliest
place the topic introduces it.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from lessons.models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
)

from .services.concept_units import concepts_for_topic


class ConceptUnitTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.module = OutlineNode.objects.create(
            course=self.course, title="Properties of Matter", order=0, depth=0
        )
        self.topic = OutlineNode.objects.create(
            course=self.course, parent=self.module,
            title="Solid, Liquid and Gas", order=0, depth=1,
        )
        now = timezone.now()
        self.first = self._material("Lesson one", now)
        self.second = self._material("Lesson two", now + timedelta(hours=1))

    def _material(self, title, created_at):
        material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic,
            title=title, status="completed",
        )
        # Upload order decides which file counts as "earlier in the topic", so
        # it is set explicitly rather than left to row creation timing.
        LearningMaterial.objects.filter(pk=material.pk).update(created_at=created_at)
        material.refresh_from_db()
        return material

    def _group(self, label=""):
        return LearningObjectGroup.objects.create(outline_node=self.topic, label=label)

    def _object(self, material, order, title, content="Some text.", group=None, represented_by=None):
        return LearningObject.objects.create(
            material=material, group=group, title=title, content=content,
            order=order, represented_by=represented_by,
        )

    def _by_id(self, concepts):
        return {concept.id: concept for concept in concepts}

    def test_a_concept_keeps_its_members_from_every_file(self):
        group = self._group("Solid")
        original = self._object(self.first, 0, "Solid", "A solid keeps its shape.", group)
        self._object(self.second, 3, "Solids", "Particles are packed tight.", group, original)

        concept = self._by_id(concepts_for_topic(self.topic))[group.id]

        self.assertEqual(len(concept.members), 2)
        self.assertEqual(concept.source_material_ids, sorted([self.first.id, self.second.id]))

    def test_a_represented_member_is_not_dropped(self):
        """Regression: filtering these out hid cross-PDF grouping entirely.

        At learning-object level `represented_by` means "taught through another
        object, so do not sequence it". Here the concept *is* the step, and a
        represented member is simply another file's version of it. Dropping
        them made every concept report a single source.
        """
        group = self._group("Gas")
        original = self._object(self.first, 0, "Gas", "A gas spreads out.", group)
        represented = self._object(
            self.second, 1, "Gases", "Gas particles move freely.", group, original
        )

        concept = self._by_id(concepts_for_topic(self.topic))[group.id]

        self.assertIn(represented, concept.members)

    def test_the_representative_is_never_a_represented_member(self):
        group = self._group("Liquid")
        original = self._object(self.first, 0, "Liquid", "A liquid flows.", group)
        self._object(self.second, 1, "Liquids", "It takes its container's shape.", group, original)

        concept = self._by_id(concepts_for_topic(self.topic))[group.id]

        self.assertIsNone(concept.representative.represented_by_id)
        self.assertIn(concept.representative, concept.members)

    def test_the_representative_supplies_the_concepts_text(self):
        group = self._group()
        self._object(self.first, 0, "Matter", "Matter has mass.", group)

        concept = self._by_id(concepts_for_topic(self.topic))[group.id]

        # No group label, so the representative names the concept too.
        self.assertEqual(concept.title, "Matter")
        self.assertEqual(concept.content, "Matter has mass.")

    def test_the_current_object_title_names_the_concept_not_a_stale_label(self):
        """A group's label is set once, when the group forms. After a teacher
        renames the object, the path must show the new name."""
        group = self._group("Diagram description (Part 1 of 2)")
        self._object(self.first, 0, "Diagram description for Solid", "Dots in rows.", group)

        concept = self._by_id(concepts_for_topic(self.topic))[group.id]

        self.assertEqual(concept.title, "Diagram description for Solid")

    def _titles(self):
        return [concept.title for concept in concepts_for_topic(self.topic)]

    def _sequence(self, material, titles):
        """Give ``material`` concepts in exactly this order, sharing groups by title."""
        for order, title in enumerate(titles):
            group = self._shared_groups.get(title)
            if group is None:
                group = self._shared_groups[title] = self._group(title)
            self._object(material, order, title, f"About {title}.", group)

    def test_a_concept_only_one_file_teaches_keeps_its_neighbours(self):
        """Regression: sorting file by file pushed every one-file concept behind
        the whole of the earlier file, so a solid diagram landed after Gas."""
        self._shared_groups = {}
        self._sequence(self.first, ["Matter", "Solid", "Liquid", "Gas"])
        self._sequence(self.second, ["Matter", "Solid", "Solid diagram", "Liquid", "Gas"])

        self.assertEqual(self._titles(), ["Matter", "Solid", "Solid diagram", "Liquid", "Gas"])

    def test_a_disagreement_follows_the_file_with_more_concepts(self):
        """One file opens with a comparison; the fuller one shows it after all
        three states. With two files it is a tie, and the fuller file decides."""
        self._shared_groups = {}
        self._sequence(self.first, ["Comparing", "Matter", "Solid", "Liquid", "Gas"])
        self._sequence(self.second, ["Matter", "Solid", "Liquid", "Gas", "Comparing", "Examples"])

        self.assertEqual(
            self._titles(), ["Matter", "Solid", "Liquid", "Gas", "Comparing", "Examples"],
        )

    def test_between_files_of_the_same_size_the_first_uploaded_decides(self):
        self._shared_groups = {}
        self._sequence(self.first, ["Matter", "Solid", "Liquid", "Gas", "Comparing"])
        self._sequence(self.second, ["Matter", "Comparing", "Solid", "Liquid", "Gas"])

        self.assertEqual(self._titles(), ["Matter", "Solid", "Liquid", "Gas", "Comparing"])

    def test_the_result_is_always_an_order_some_file_actually_uses(self):
        """Blending positions put Comparing between Liquid and Gas here -- an
        order neither author wrote. Every pair must follow a real file."""
        self._shared_groups = {}
        first = ["Matter", "Solid", "Liquid", "Gas", "Comparing"]
        second = ["Matter", "Comparing", "Solid", "Liquid", "Gas"]
        self._sequence(self.first, first)
        self._sequence(self.second, second)

        self.assertIn(self._titles(), [first, second])

    def test_with_three_files_the_majority_outvotes_the_fuller_file(self):
        self._shared_groups = {}
        third = self._material("Lesson three", timezone.now() + timedelta(hours=2))
        # The fuller file puts Comparing first; the two smaller files agree on last.
        self._sequence(self.first, ["Matter", "Solid", "Gas", "Comparing"])
        self._sequence(self.second, ["Comparing", "Matter", "Solid", "Gas", "Liquid", "Examples"])
        self._sequence(third, ["Matter", "Solid", "Gas", "Comparing"])

        titles = self._titles()

        self.assertGreater(titles.index("Comparing"), titles.index("Gas"))

    def test_a_concept_is_placed_by_its_earliest_member_not_its_latest(self):
        early = self._group("Early")
        self._object(self.first, 1, "Early concept", "Text.", early)

        spanning = self._group("Spanning")
        original = self._object(self.first, 0, "Spanning concept", "Text.", spanning)
        # Also appears near the end of the second file; the early appearance wins.
        self._object(self.second, 20, "Spanning again", "Text.", spanning, original)

        concepts = self._by_id(concepts_for_topic(self.topic))

        self.assertLess(concepts[spanning.id].order, concepts[early.id].order)

    def test_positions_are_contiguous_from_zero(self):
        for index in range(4):
            group = self._group(f"Concept {index}")
            self._object(self.first, index, f"Concept {index}", "Text.", group)

        orders = [concept.order for concept in concepts_for_topic(self.topic)]

        self.assertEqual(orders, list(range(4)))

    def test_a_split_passage_becomes_one_concept(self):
        """The chunker's "(Part 1 of 3)" pieces are one passage, and grouping
        scatters them: part 1 sits with the other files' versions of the
        concept, parts 2 and 3 sit alone. Left that way the concept appears in
        the path three times."""
        opening = self._group("Solid")
        first = self._object(self.first, 0, "SOLID (Part 1 of 3)", "A solid keeps its shape.", opening)
        second_group = self._group()
        self._object(self.first, 1, "SOLID (Part 2 of 3)", "Its particles vibrate.", second_group)
        third_group = self._group()
        self._object(self.first, 2, "SOLID (Part 3 of 3)", "It does not flow.", third_group)

        concepts = concepts_for_topic(self.topic)

        self.assertEqual(len(concepts), 1)
        self.assertEqual(concepts[0].id, opening.id)
        self.assertEqual(len(concepts[0].members), 3)
        self.assertEqual(concepts[0].representative, first)

    def test_a_merged_split_passage_reads_in_document_order_not_group_order(self):
        """Regression: merging concatenates each source group's members in
        the order `_merge_split_passages` visits the groups -- their queryset
        order, which has no relation to the document. Creating part 2's group
        before part 1's flips that concatenation unless the result is
        re-sorted by document position afterwards."""
        second_group = self._group()
        self._object(self.first, 1, "SOLID (Part 2 of 2)", "Its particles vibrate.", second_group)
        opening = self._group("Solid")
        self._object(self.first, 0, "SOLID (Part 1 of 2)", "A solid keeps its shape.", opening)

        concepts = concepts_for_topic(self.topic)

        self.assertEqual(len(concepts), 1)
        self.assertEqual(
            concepts[0].member_text,
            "A solid keeps its shape.\nIts particles vibrate.",
        )

    def test_repeated_titles_are_told_apart_by_position(self):
        """One file carries three passages all titled "Diagram description
        (Part 1 of 2)" / "(Part 2 of 2)", one per state of matter. Matching on
        the title alone fuses all three into a single nonsense concept; they are
        separated by sitting next to each other in the document."""
        for start in (0, 4, 8):
            for number in (1, 2):
                group = self._group()
                self._object(
                    self.first, start + number - 1,
                    f"Diagram description (Part {number} of 2)", "Dots in a pattern.", group,
                )

        concepts = concepts_for_topic(self.topic)

        self.assertEqual(len(concepts), 3)
        self.assertEqual([len(concept.members) for concept in concepts], [2, 2, 2])

    def test_an_incomplete_series_is_left_alone(self):
        """Part 2 of 3 with no part 3 is not a whole passage, and guessing at
        the gap would join text that may not belong together."""
        first_group = self._group()
        self._object(self.first, 0, "SOLID (Part 1 of 3)", "A solid keeps its shape.", first_group)
        second_group = self._group()
        self._object(self.first, 1, "SOLID (Part 2 of 3)", "Its particles vibrate.", second_group)

        concepts = concepts_for_topic(self.topic)

        self.assertEqual(len(concepts), 2)

    def test_overlapping_passages_merge_together(self):
        """A group can belong to two passages at once -- grouping put one file's
        diagram with another file's liquid text. Letting whichever run is seen
        first win would absorb that group and orphan the diagram's other half."""
        liquid = self._group("Liquid")
        self._object(self.first, 0, "LIQUID (Part 1 of 2)", "A liquid flows.", liquid)

        shared = self._group()
        second_part = self._object(
            self.first, 1, "LIQUID (Part 2 of 2)", "It takes its container's shape.", shared
        )
        # The same group also carries the opening of another file's passage.
        self._object(
            self.second, 0, "Diagram description (Part 1 of 2)", "Dots close together.",
            shared, second_part,
        )
        tail = self._group()
        self._object(
            self.second, 1, "Diagram description (Part 2 of 2)", "They slide past each other.", tail
        )

        concepts = concepts_for_topic(self.topic)

        self.assertEqual(len(concepts), 1)
        self.assertEqual(concepts[0].id, liquid.id)
        self.assertEqual(len(concepts[0].members), 4)

    def test_a_group_with_no_members_is_skipped(self):
        self._group("Empty")
        kept = self._group("Kept")
        self._object(self.first, 0, "Kept", "Text.", kept)

        concepts = concepts_for_topic(self.topic)

        self.assertEqual([concept.id for concept in concepts], [kept.id])


class BundleConceptTests(TestCase):
    """The concept's title and text come from its members' bundle order, not
    from a single member's scan position -- see `lessons.services.concept_bundles`.
    """

    def setUp(self):
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.module = OutlineNode.objects.create(
            course=self.course, title="Properties of Matter", order=0, depth=0
        )
        self.topic = OutlineNode.objects.create(
            course=self.course, parent=self.module,
            title="Solid, Liquid and Gas", order=0, depth=1,
        )
        now = timezone.now()
        self.first = self._material("Lesson one", now)
        self.second = self._material("Lesson two", now + timedelta(hours=1))

    def _material(self, title, created_at):
        material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic,
            title=title, status="completed",
        )
        LearningMaterial.objects.filter(pk=material.pk).update(created_at=created_at)
        material.refresh_from_db()
        return material

    def _group(self, label=""):
        return LearningObjectGroup.objects.create(outline_node=self.topic, label=label)

    def _object(self, group, material, title, order, content="Some text.", section=""):
        return LearningObject.objects.create(
            material=material, group=group, title=title, content=content,
            order=order, section_title=section,
        )

    def _concept_for(self, group):
        return {concept.id: concept for concept in concepts_for_topic(self.topic)}[group.id]

    def test_a_concept_is_named_by_its_headings_not_its_first_object(self):
        """Regression: a bundle opening with a figure was named after the
        figure, and the concept then had no usable name for the criteria."""
        group = self._group("Matter")
        self._object(group, self.first, "Okay, let us describe this figure", 0, section="")
        self._object(group, self.first, "Matter", 1, section="Matter")

        concept = self._concept_for(group)

        self.assertEqual(concept.title, "Matter")

    def test_member_text_follows_bundle_order(self):
        group = self._group("Solid")
        self._object(group, self.first, "Solid", 0, content="A solid keeps its shape.")
        self._object(group, self.second, "Solids", 0, content="Packed tightly.")
        self._object(group, self.second, "Everyday examples", 1, content="Ice cubes.")

        concept = self._concept_for(group)

        self.assertEqual(
            concept.member_text,
            "A solid keeps its shape.\nPacked tightly.\nIce cubes.",
        )

    def test_a_single_object_bundle_keeps_its_own_title_not_the_shared_heading(self):
        """Regression from real material: "Solid", "Liquid" and "Gas" each sit
        alone under one section, "Matter". A bundle with only one object has
        no figure-first problem for the heading to fix, and the section names
        the whole section, not any one object in it -- titling all three
        "Matter" would give them one shared name, and the criteria's
        same-name veto would then delete every edge between them."""
        solid = self._group("Solid")
        self._object(solid, self.first, "Solid", 0, section="Matter")
        liquid = self._group("Liquid")
        self._object(liquid, self.first, "Liquid", 1, section="Matter")
        gas = self._group("Gas")
        self._object(gas, self.first, "Gas", 2, section="Matter")

        titles = {self._concept_for(group).title for group in (solid, liquid, gas)}

        self.assertEqual(titles, {"Solid", "Liquid", "Gas"})

    def test_a_split_passage_does_not_borrow_its_sections_heading(self):
        """Finding 8: `_merge_split_passages` folds other groups' members into
        one concept, so the representative's "bundle" could count parts merged
        in from elsewhere and cross the two-object threshold that takes the
        section heading. Real material puts "SOLID" and "LIQUID" under one
        "Matter" section; naming both concepts "Matter" hands the criteria's
        same-name veto every edge between them -- exactly what amending §3.5
        was meant to prevent."""
        solid_first = self._group("Solid")
        self._object(solid_first, self.first, "SOLID (Part 1 of 2)", 0, section="Matter")
        solid_rest = self._group("Solid rest")
        self._object(solid_rest, self.first, "SOLID (Part 2 of 2)", 1, section="Matter")
        liquid_first = self._group("Liquid")
        self._object(liquid_first, self.first, "LIQUID (Part 1 of 2)", 2, section="Matter")
        liquid_rest = self._group("Liquid rest")
        self._object(liquid_rest, self.first, "LIQUID (Part 2 of 2)", 3, section="Matter")

        titles = [concept.title for concept in concepts_for_topic(self.topic)]

        self.assertEqual(len(titles), 2)
        self.assertEqual(len(set(titles)), 2, f"both concepts were named the same: {titles}")
        self.assertNotIn("Matter", titles)

    def test_a_merged_split_passages_fallback_title_drops_the_part_marker(self):
        """Regression: when the representative's own bundle has only one
        object, the title falls back to `representative.title` -- but a
        merged split passage's representative is still titled with the
        chunker's "(Part 1 of 2)" marker. Left unstripped, that marker
        reaches the teacher's concept list and is what the criteria match
        against."""
        opening = self._group("SOLID")
        self._object(opening, self.first, "SOLID (Part 1 of 2)", 0)
        rest = self._group("SOLID rest")
        self._object(rest, self.first, "SOLID (Part 2 of 2)", 1)

        concept = self._concept_for(opening)

        self.assertEqual(concept.title, "SOLID")
