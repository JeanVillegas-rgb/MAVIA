from django.test import TestCase

from lessons.models import CourseGroup, LearningMaterial, LearningObject, OutlineNode

from .services.evidence import MEDIUM, STRONG, Evidence, accept


class AcceptanceRuleTests(TestCase):
    def _ev(self, tier, name):
        return Evidence(type=name, tier=tier, detail=None)

    def test_one_strong_accepts(self):
        self.assertTrue(accept([self._ev(STRONG, "explicit_dependency")], []))

    def test_one_medium_does_not_accept(self):
        self.assertFalse(accept([], [self._ev(MEDIUM, "definitional")]))

    def test_two_medium_of_different_types_accept(self):
        self.assertTrue(accept([], [
            self._ev(MEDIUM, "definitional"),
            self._ev(MEDIUM, "instantiation"),
        ]))

    def test_two_medium_of_the_same_type_do_not_accept(self):
        """One kind of observation seen twice is not corroboration."""
        self.assertFalse(accept([], [
            self._ev(MEDIUM, "instantiation"),
            self._ev(MEDIUM, "instantiation"),
        ]))

    def test_nothing_accepts_nothing(self):
        self.assertFalse(accept([], []))


class MaterialFixture(TestCase):
    """A material under a confirmed outline: module names the broader concept,
    topic enumerates its subjects."""

    def setUp(self):
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.module = OutlineNode.objects.create(
            course=self.course, title="Properties of Matter", order=0, depth=0
        )
        self.topic = OutlineNode.objects.create(
            course=self.course, parent=self.module,
            title="Solid, Liquid and Gas", order=0, depth=1,
        )
        self.material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic,
            title="States", status="completed",
        )

    def _object(self, order, title, content, section_title=""):
        return LearningObject.objects.create(
            material=self.material, title=title, content=content,
            section_title=section_title, order=order,
        )


class ScopeFramingTests(MaterialFixture):
    """S3 -- the answer to the phrasing problem.

    One of the two real PDFs never writes "a solid is a state of matter", so S2
    cannot fire on it. The teacher-confirmed outline still says the lesson sits
    under 'Properties of Matter' and covers 'Solid, Liquid and Gas'.
    """

    def test_scope_framing_links_the_module_concept_to_the_topic_subjects(self):
        from .services.evidence import build_context

        matter = self._object(0, "Matter", "Matter is anything that has mass.")
        solid = self._object(1, "Solid", "A solid has a definite shape.")
        gas = self._object(2, "Gas", "A gas spreads out to fill its container.")

        ctx = build_context(self.material, [matter, solid, gas])

        self.assertEqual(ctx.scope_concept, "matter")
        self.assertIn(solid.id, ctx.subject_ids)
        self.assertIn(gas.id, ctx.subject_ids)
        self.assertNotIn(matter.id, ctx.subject_ids)

    def test_scope_framing_needs_no_is_a_sentence(self):
        from .services.evidence import build_context, gather

        matter = self._object(0, "Matter", "Matter is anything that has mass.")
        solid = self._object(1, "Solid", "A solid has a definite shape.")
        ctx = build_context(self.material, [matter, solid])

        strong, _, _ = gather(matter, solid, ctx)

        self.assertIn("scope_framing", {e.type for e in strong})

    def test_the_scope_concept_does_not_depend_on_its_own_subjects(self):
        from .services.evidence import build_context, gather

        matter = self._object(0, "Matter", "Matter is anything that has mass.")
        solid = self._object(1, "Solid", "A solid has a definite shape.")
        ctx = build_context(self.material, [matter, solid])

        strong, _, _ = gather(solid, matter, ctx)

        self.assertNotIn("scope_framing", {e.type for e in strong})


class ConditionalIsATests(MaterialFixture):
    def test_is_a_is_strong_when_the_parent_is_taught_here(self):
        from .services.evidence import build_context, gather

        matter = self._object(0, "Matter", "Matter is anything that has mass.")
        solid = self._object(1, "Solid", "A solid is a state of matter.")
        ctx = build_context(self.material, [matter, solid])

        strong, _, _ = gather(matter, solid, ctx)

        self.assertIn("is_a", {e.type for e in strong})

    def test_is_a_is_not_strong_when_the_parent_is_never_taught(self):
        """"A whale is a mammal" is not an instructional dependency unless the
        lesson also teaches mammals."""
        from .services.evidence import build_context, gather

        whale = self._object(0, "Whale", "A whale is a kind of mammal.")
        other = self._object(1, "Shark", "A shark is a kind of fish.")
        ctx = build_context(self.material, [whale, other])

        strong, _, _ = gather(other, whale, ctx)

        self.assertNotIn("is_a", {e.type for e in strong})


class SuppressorOrderingTests(MaterialFixture):
    """The bug an earlier draft of the spec had: suppressors ran before strong
    evidence was gathered, so the documented S1 override was unreachable."""

    def test_siblings_are_suppressed_without_an_explicit_statement(self):
        from .services.evidence import build_context, suppressed

        solid = self._object(0, "Solid", "A solid is a state of matter.")
        liquid = self._object(1, "Liquid", "A liquid is a state of matter.")
        ctx = build_context(self.material, [solid, liquid])

        self.assertTrue(suppressed(solid, liquid, ctx, author_says_so=False))

    def test_an_explicit_statement_overrides_the_sibling_suppressor(self):
        from .services.evidence import build_context, suppressed

        solid = self._object(0, "Solid", "A solid is a state of matter.")
        liquid = self._object(1, "Liquid", "A liquid is a state of matter.")
        ctx = build_context(self.material, [solid, liquid])

        self.assertFalse(suppressed(solid, liquid, ctx, author_says_so=True))

    def test_a_contrastive_mention_is_suppressed(self):
        from .services.evidence import build_context, suppressed

        solid = self._object(0, "Solid", "A solid has a definite shape.")
        flow = self._object(1, "Flow", "Liquids and gases can flow, while solids normally do not.")
        ctx = build_context(self.material, [solid, flow])

        self.assertTrue(suppressed(solid, flow, ctx, author_says_so=False))

    def test_co_definers_stay_suppressed_even_with_an_explicit_statement(self):
        from .services.evidence import build_context, suppressed

        first = self._object(0, "Solid", "A solid has a definite shape.")
        second = self._object(1, "Solid", "Solids keep their own form.")
        ctx = build_context(self.material, [first, second])

        self.assertTrue(suppressed(first, second, ctx, author_says_so=True))


class ExplicitDependencyTests(MaterialFixture):
    def test_an_explicit_statement_is_strong_evidence(self):
        from .services.evidence import build_context, gather

        solid = self._object(0, "Solid", "A solid has a definite shape.")
        changes = self._object(
            1, "Changes of State",
            "Heating and cooling cause changes, but understanding each state should come first.",
        )
        ctx = build_context(self.material, [solid, changes])

        strong, _, _ = gather(solid, changes, ctx)

        self.assertIn("explicit_dependency", {e.type for e in strong})


class WeakEvidenceTests(MaterialFixture):
    def test_a_plain_mention_never_reaches_acceptance(self):
        """body_reference produced 88% of the old graph. It can no longer
        create an edge on its own."""
        from .services.evidence import build_context, gather

        alpha = self._object(0, "Photosynthesis", "Photosynthesis feeds a plant.")
        beta = self._object(1, "Leaf", "The leaf is where photosynthesis happens.")
        ctx = build_context(self.material, [alpha, beta])

        strong, medium, weak = gather(alpha, beta, ctx)

        self.assertFalse(accept(strong, medium))
        self.assertIn("mention", {e.type for e in weak})
