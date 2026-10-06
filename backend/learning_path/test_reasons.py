"""The one-sentence reason a teacher reads beside each link."""

from django.test import SimpleTestCase

from .services.reasons import link_reason


def reference(forward, n, backward, p):
    return {"rule": "reference", "reference": {
        "prw_forward": forward, "prw_backward": backward, "prd": forward - backward,
        "theta": 0.05, "passages_forward": n, "passages_backward": p,
    }}


class LinkReasonTests(SimpleTestCase):
    def test_definition_quotes_the_sentence(self):
        evidence = {"rule": "definition", "definition": {"sentence": "Melting is when a solid turns into a liquid"}}

        self.assertEqual(
            link_reason(evidence, "Solid", "Melting"),
            "Melting's definition uses Solid: “Melting is when a solid turns into a liquid”",
        )

    def test_containment_names_the_heading(self):
        evidence = {"rule": "containment", "containment": {"heading": "matter"}}

        self.assertEqual(link_reason(evidence, "Matter", "Solid"), "Solid sits under the heading “Matter”.")

    def test_reference_counts_passages(self):
        self.assertEqual(
            link_reason(reference(0.8, 5, 0.0, 3), "Solid", "Comparing"),
            "Comparing's text names Solid in 4 of 5 passages; Solid's text never names Comparing.",
        )

    def test_reference_both_ways(self):
        self.assertEqual(
            link_reason(reference(1.0, 2, 0.5, 2), "Solid", "Comparing"),
            "Comparing's text names Solid in 2 of 2 passages; Solid's text names Comparing in 1 of 2.",
        )

    def test_reference_without_counts_still_reads(self):
        evidence = {"rule": "reference", "reference": {"prw_forward": 1.0, "prw_backward": 0.0}}

        self.assertEqual(
            link_reason(evidence, "Solid", "Comparing"),
            "Comparing's text names Solid more often than Solid's text names Comparing.",
        )

    def test_conflict(self):
        self.assertEqual(link_reason({"rule": "conflict"}, "A", "B"), "The lesson files point both ways; choose one.")

    def test_a_teacher_link_has_no_evidence(self):
        self.assertEqual(link_reason({}, "A", "B"), "Added by you.")
        self.assertEqual(link_reason(None, "A", "B"), "Added by you.")

    def test_an_accepted_v6_link_names_its_words_and_the_order(self):
        evidence = {"rule": "reference-order", "direction_from": "pdf_order", "contradictions": [],
                    "votes": {"name": 0, "terms": 1, "meaning": 1, "heading": 0, "order": 0},
                    "records": {"name": {"use": 0.0, "use_back": 0.0},
                                "terms": {"owned": ["particle", "vibrate"], "use": 0.5, "use_back": 0.0}}}

        self.assertEqual(
            link_reason(evidence, "Solid", "Gas"),
            "Gas uses terms Solid explains (particle, vibrate). Solid comes first in the lesson.",
        )

    def test_an_accepted_v6_link_mentions_words_used_both_ways(self):
        """The net vote is 0 when both use each other's terms equally; the reference is still real."""
        evidence = {"rule": "reference-order", "direction_from": "pdf_order", "contradictions": [],
                    "votes": {"name": 0, "terms": 0, "heading": 0},
                    "records": {"name": {"use": 0.0, "use_back": 0.0},
                                "terms": {"owned": ["mixture"], "use": 1.0, "use_back": 1.0}}}

        self.assertEqual(
            link_reason(evidence, "Uniform mixtures", "Air"),
            "Air uses terms Uniform mixtures explains (mixture). Uniform mixtures comes first in the lesson.",
        )

    def test_a_link_through_its_parts_names_them(self):
        evidence = {"rule": "reference-order", "direction_from": "pdf_order", "contradictions": [],
                    "votes": {"name": 1, "terms": 0, "heading": 0},
                    "records": {"name": {"use": 0.75, "use_back": 0.0, "parts": ["Pollination", "Fertilization"]},
                                "terms": {"use": 0.0, "use_back": 0.0}}}

        self.assertEqual(
            link_reason(evidence, "How Flowering Plants Reproduce", "Everyday Examples"),
            "Everyday Examples names Pollination and Fertilization, parts of How Flowering Plants Reproduce. "
            "How Flowering Plants Reproduce comes first in the lesson.",
        )

    def test_many_parts_are_counted_not_listed(self):
        evidence = {"rule": "reference-order", "direction_from": "pdf_order", "contradictions": [],
                    "records": {"name": {"use": 0.5, "use_back": 0.0,
                                         "parts": ["Mouth", "Stomach", "Small intestine"]}}}

        self.assertTrue(link_reason(evidence, "The Digestive System", "Helper organs").startswith(
            "Helper organs names Mouth, Stomach and 1 more, parts of The Digestive System."))

    def test_a_v6_link_by_heading_and_name(self):
        evidence = {"rule": "reference-order", "direction_from": "heading", "contradictions": [],
                    "votes": {"name": 1, "terms": 0, "heading": 1},
                    "records": {"name": {"use": 0.5, "use_back": 0.0}, "terms": {"use": 0.0, "use_back": 0.0}}}

        self.assertEqual(
            link_reason(evidence, "Matter", "Solid"),
            "Solid sits under a heading naming Matter. Solid names Matter.",
        )

    def test_a_v6_suggestion_says_what_stands_against_it(self):
        evidence = {"rule": "reference-order", "direction_from": "pdf_order",
                    "contradictions": ["reverse_name", "backward_only"],
                    "votes": {"name": -1, "terms": 0, "heading": 0},
                    "records": {"name": {"use": 0.0, "use_back": 0.5}, "terms": {"use": 0.0, "use_back": 0.0}}}

        self.assertEqual(
            link_reason(evidence, "Matter", "Solid"),
            "Matter comes first in the lesson. But Matter's text names Solid more than the reverse. "
            "But only Matter's text refers to Solid.",
        )

    def test_a_v6_figure_suggestion_placed_before_its_text_reads_the_right_way(self):
        """Stored contradictions are about the PDF's earlier/later; here the figure came first
        and the link points back, so "backward_only" must not be read as Solid's."""
        evidence = {"rule": "reference-order", "direction_from": "figure", "contradictions": ["figure", "backward_only"],
                    "votes": {"name": 0, "terms": 1, "heading": 0},
                    "records": {"name": {"use": 0.0, "use_back": 0.0},
                                "terms": {"owned": ["particle"], "use": 0.6, "use_back": 0.0}}}

        self.assertEqual(
            link_reason(evidence, "Solid", "Particles in a solid"),
            "Particles in a solid uses terms Solid explains (particle). "
            "The figure's description refers to Solid. "
            "One of them is a figure, so its place in the file does not give the order.",
        )

    def test_a_v6_suggestion_directed_by_the_names_reads_the_right_way(self):
        evidence = {"rule": "reference-order", "direction_from": "name",
                    "contradictions": ["reverse_name", "no_shared_pdf"],
                    "votes": {"name": 1, "terms": -1, "heading": 0},
                    "records": {"name": {"use": 0.3, "use_back": 0.0},
                                "terms": {"owned": [], "use": 0.0, "use_back": 0.5}}}

        self.assertEqual(
            link_reason(evidence, "Stamen", "Pollination"),
            "Pollination names Stamen. The names put Stamen first; the files do not settle the order. "
            "They come from different files, so their order is a guess.",
        )

    def test_v6_suggestions_from_different_or_disagreeing_files(self):
        evidence = {"rule": "reference-order", "direction_from": "merged_order",
                    "contradictions": ["no_shared_pdf"], "votes": {"terms": 1},
                    "records": {"name": {"use": 0.0, "use_back": 0.0},
                                "terms": {"owned": [], "use": 0.5, "use_back": 0.0}}}

        self.assertEqual(
            link_reason(evidence, "Stamen", "Pollination"),
            "Pollination uses terms Stamen explains. Stamen comes first in the topic's combined order. "
            "They come from different files, so their order is a guess.",
        )
        evidence["contradictions"] = ["pdfs_disagree"]
        self.assertTrue(link_reason(evidence, "Stamen", "Pollination").endswith("The files put them in different orders."))

    def test_a_v6_suggestion_from_the_files_alone(self):
        evidence = {"rule": "reference-order", "direction_from": "pdf_agreement", "contradictions": [],
                    "votes": {"order": 1}, "records": {"order": {"pdfs": 2, "agree": 2}}}

        self.assertEqual(
            link_reason(evidence, "Pollination", "Fertilization"),
            "2 of 2 files teach Pollination first; the text says nothing either way.",
        )

    def test_a_fused_link_names_its_clues(self):
        evidence = {
            "rule": "fusion", "confidence": 0.81, "semantic": True,
            "votes": {"name": 0, "terms": 1, "meaning": 1, "order": 1},
            "records": {"terms": {"owned": ["anther", "filament"]}, "order": {"pdfs": 2, "agree": 2}},
        }

        self.assertEqual(
            link_reason(evidence, "Stamen", "Pollination"),
            "Pollination uses terms Stamen explains (anther, filament). "
            "Pollination's sentences refer to Stamen's ideas. "
            "2 of 2 files teach Stamen first. Confidence 0.81.",
        )

    def test_a_fused_link_says_which_clues_disagree_and_when_meaning_was_unavailable(self):
        evidence = {"rule": "fusion", "confidence": 0.3, "semantic": False,
                    "votes": {"name": 1, "terms": 0, "meaning": 0, "order": -1}, "records": {}}

        self.assertEqual(
            link_reason(evidence, "Stamen", "Pollination"),
            "Pollination names Stamen. Against it: the files' order. "
            "The meaning check was unavailable. Confidence 0.30.",
        )

    def test_a_fused_link_against_the_text_says_so(self):
        evidence = {"rule": "fusion", "confidence": 0.5, "semantic": True, "disagreement": True, "parallel": False,
                    "votes": {"name": -1, "terms": 0, "meaning": 0, "heading": 1, "order": 0}, "records": {}}

        self.assertEqual(
            link_reason(evidence, "Matter", "Solid"),
            "Solid sits under a heading naming Matter. Against it: the name. "
            "The text reads the other way; this follows how the files are organised. Confidence 0.50.",
        )

    def test_siblings_are_explained(self):
        evidence = {"rule": "fusion", "confidence": 1.0, "semantic": True, "disagreement": False, "parallel": True,
                    "votes": {"name": 1, "terms": 0, "meaning": 0, "heading": 0, "order": 0}, "records": {}}

        self.assertEqual(
            link_reason(evidence, "Solid", "Gas"),
            "Gas names Solid. The files present Solid and Gas side by side under one heading. Confidence 1.00.",
        )

    def test_a_suggestion_from_the_files_alone_says_the_text_is_silent(self):
        evidence = {"rule": "fusion", "confidence": 1.0, "semantic": True, "disagreement": False, "parallel": False,
                    "votes": {"name": 0, "terms": 0, "meaning": 0, "heading": 0, "order": 1},
                    "records": {"order": {"pdfs": 2, "agree": 2}}}

        self.assertEqual(
            link_reason(evidence, "Pollination", "Fertilization"),
            "2 of 2 files teach Pollination first. The text says nothing either way. Confidence 1.00.",
        )

    def test_a_course_link_that_follows_the_outline(self):
        evidence = {"rule": "course", "confidence": 1.0, "semantic": True, "contradicts_outline": False,
                    "votes": {"name": 1, "terms": 1, "meaning": 0, "outline": 1},
                    "records": {"terms": {"owned": ["anther"]}}}

        self.assertEqual(
            link_reason(evidence, "Stamen", "Pollination"),
            "Pollination uses terms Stamen explains (anther). Pollination names Stamen. "
            "This follows your outline. Confidence 1.00.",
        )

    def test_a_course_link_against_the_outline_says_so(self):
        evidence = {"rule": "course", "confidence": 0.67, "semantic": True, "contradicts_outline": True,
                    "votes": {"name": 1, "terms": 1, "meaning": 0, "outline": -1}, "records": {}}

        self.assertEqual(
            link_reason(evidence, "Stamen", "Pollination"),
            "Pollination uses terms Stamen explains. Pollination names Stamen. "
            "This contradicts your outline: Stamen's topic comes later. Confidence 0.67.",
        )


def three_vote_evidence(direction_from, hierarchy=0, order=0, reference=0, hierarchy_from=""):
    return {"rule": "three-votes", "direction_from": direction_from,
            "votes": {"hierarchy": hierarchy, "order": order, "reference": reference},
            "records": {"hierarchy": {"from": hierarchy_from}, "order": {"centres": {}}, "reference": {}}}


class ThreeVoteReasonTests(SimpleTestCase):
    def test_an_outvoted_order_is_said_plainly(self):
        evidence = three_vote_evidence("outvoted_order", hierarchy=1, order=-1, reference=1, hierarchy_from="subsumption")

        self.assertEqual(
            link_reason(evidence, "Matter", "Solid"),
            "Matter is the broader idea: almost everywhere Solid appears, Matter does too. "
            "Solid refers to Matter more than the reverse. "
            "This outvoted the lesson order, which teaches Solid first.",
        )

    def test_votes_that_agree(self):
        evidence = three_vote_evidence("votes_agree", hierarchy=1, order=1, hierarchy_from="heading")

        self.assertEqual(
            link_reason(evidence, "Seeds", "Germination"),
            "Germination sits under a heading naming Seeds. The lesson teaches Seeds first.",
        )

    def test_order_only_says_how_weak_it_is(self):
        self.assertEqual(
            link_reason(three_vote_evidence("order_only", order=1), "Pollination", "Fertilization"),
            "The lesson teaches Pollination first. "
            "Direction from the lesson order only; the text gives nothing else to go on.",
        )

    def test_a_contested_suggestion_names_what_stands_against_it(self):
        evidence = three_vote_evidence("contested", hierarchy=-1, order=1)

        self.assertEqual(
            link_reason(evidence, "Stamen", "Pollination"),
            "The lesson teaches Stamen first. Against it: the hierarchy. "
            "The evidence disagrees; choose the direction.",
        )


class SingleWordReasonTests(SimpleTestCase):
    def evidence(self, owned=(), owned_back=()):
        return {"rule": "reference-order", "version": "6.1", "direction_from": "pdf_order",
                "contradictions": ["weak_terms"], "votes": {"name": 0, "terms": 0, "heading": 0},
                "records": {"name": {"use": 0.0, "use_back": 0.0},
                            "terms": {"owned": list(owned), "owned_back": list(owned_back),
                                      "use": 0.0, "use_back": 0.0}}}

    def test_it_names_the_word(self):
        self.assertEqual(
            link_reason(self.evidence(owned=["table"]), "Comparing the Three States", "Everyday Example"),
            "Comparing the Three States comes first in the lesson. "
            "Only one shared word links them (table); please confirm.",
        )

    def test_it_names_the_word_when_the_prerequisite_uses_it(self):
        self.assertEqual(
            link_reason(self.evidence(owned_back=["stigma"]), "Pollination", "Pistil"),
            "Pollination comes first in the lesson. Only one shared word links them (stigma); please confirm.",
        )

    def test_several_single_words_read_in_the_plural(self):
        """Each passage holds one word, but different passages hold different ones."""
        self.assertEqual(
            link_reason(self.evidence(owned=["energy", "movement"], owned_back=["spread"]), "As a general rule", "Gas"),
            "As a general rule comes first in the lesson. "
            "Only single shared words link them (energy, movement, spread); please confirm.",
        )


class ShortlistReasonTests(SimpleTestCase):
    def test_an_unconfirmed_entry_says_it_is_a_close_match(self):
        evidence = {"rule": "course-shortlist", "confirmed": False, "rank": 2, "votes": {}, "records": {}}

        self.assertEqual(
            link_reason(evidence, "Liquid", "Solutions"),
            "One of the 3 closest matches for Solutions in its earlier topic (rank 2). Please confirm or dismiss.",
        )

    def test_a_confirmed_entry_reads_like_a_course_link(self):
        evidence = {"rule": "course-shortlist", "confirmed": True, "rank": 1,
                    "votes": {"name": 1, "terms": 1}, "records": {"terms": {"owned": ["flow"]}}}

        self.assertEqual(
            link_reason(evidence, "Liquid", "Solutions"),
            "Solutions uses terms Liquid explains (flow). Solutions names Liquid. This follows your outline.",
        )


class ClosestReasonTests(SimpleTestCase):
    def test_an_accepted_closest_match_names_its_evidence(self):
        evidence = {"rule": "course-closest", "confirmed": True, "shared_words": ["anther", "pollen"]}

        self.assertEqual(
            link_reason(evidence, "Stamen", "Pollination"),
            "Pollination is closest in meaning to Stamen, names it, and shares anther, pollen.",
        )

    def test_a_suggested_closest_match_asks_the_teacher(self):
        evidence = {"rule": "course-closest", "confirmed": False, "shared_words": ["pollen"]}

        self.assertEqual(
            link_reason(evidence, "Stamen", "Pollination"),
            "Pollination is closest in meaning to Stamen, clearly closer than the concepts of its own "
            "topic. Please confirm or dismiss.",
        )
