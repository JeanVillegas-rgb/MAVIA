import json
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from .services import question_generator as qg
from .services.pipeline import QUESTION_DISTRIBUTION, thinking_order_counts


CONTENT = "A solid keeps a fixed shape. A liquid takes the shape of its container."


def _response(items):
    return json.dumps({"questions": items})


MCQ_ITEM = {
    "question": "What keeps a fixed shape?",
    "format": "MCQ",
    "choices": {"A": "A solid", "B": "A liquid", "C": "A gas", "D": "A plasma"},
    "correct_answer": "A",
    "explanation": "Solids hold their shape.",
}
TF_ITEM = {
    "question": "A liquid keeps a fixed shape.",
    "format": "TF",
    "correct_answer": "False",
    "explanation": "A liquid takes its container's shape.",
}


class StructuredOutputTests(SimpleTestCase):
    def test_ollama_call_sends_a_json_schema(self):
        with patch("question_generation.services.question_generator.requests.post") as post:
            post.return_value.json.return_value = {"response": _response([MCQ_ITEM])}
            post.return_value.raise_for_status.return_value = None
            qg._ollama_generate("prompt", schema=qg.build_response_schema({"MCQ": 1}))

        sent = post.call_args.kwargs["json"]
        self.assertIn("format", sent)
        self.assertEqual(sent["format"]["type"], "object")
        self.assertIn("questions", sent["format"]["properties"])

    def test_temperature_is_not_lowered(self):
        # The schema constrains shape, not content. Dropping temperature would
        # cost question variety without preventing anything.
        with patch("question_generation.services.question_generator.requests.post") as post:
            post.return_value.json.return_value = {"response": _response([MCQ_ITEM])}
            post.return_value.raise_for_status.return_value = None
            qg._ollama_generate("prompt", schema=qg.build_response_schema({"MCQ": 1}))

        self.assertEqual(post.call_args.kwargs["json"]["options"]["temperature"], 0.7)

    def test_schema_allows_both_formats(self):
        schema = qg.build_response_schema({"MCQ": 2, "TF": 1})
        item = schema["properties"]["questions"]["items"]
        self.assertEqual(set(item["properties"]["format"]["enum"]), {"MCQ", "TF"})
        # choices must be optional, or every true/false item violates the schema
        self.assertNotIn("choices", item.get("required", []))

    def test_single_format_schema_pins_the_enum(self):
        schema = qg.build_response_schema({"MCQ": 3})
        item = schema["properties"]["questions"]["items"]
        self.assertEqual(item["properties"]["format"]["enum"], ["MCQ"])


class MixedFormatValidationTests(SimpleTestCase):
    def test_validates_each_item_by_its_own_format(self):
        self.assertTrue(qg._validate_question(dict(MCQ_ITEM), "MCQ"))
        self.assertTrue(qg._validate_question(dict(TF_ITEM), "TF"))

    def test_true_false_item_is_not_rejected_for_lacking_choices(self):
        self.assertTrue(qg._validate_question(dict(TF_ITEM), "TF"))

    def test_mcq_without_choices_is_rejected(self):
        broken = {k: v for k, v in MCQ_ITEM.items() if k != "choices"}
        self.assertFalse(qg._validate_question(broken, "MCQ"))


class MixedGenerationTests(SimpleTestCase):
    @patch("question_generation.services.question_generator._ollama_generate")
    def test_one_call_returns_both_formats(self, generate):
        generate.return_value = _response([MCQ_ITEM, MCQ_ITEM, TF_ITEM])

        result = qg.generate_questions(CONTENT, "LOT", {"MCQ": 2, "TF": 1})

        self.assertEqual(generate.call_count, 1)
        self.assertEqual([q["format"] for q in result], ["MCQ", "MCQ", "TF"])

    @patch("question_generation.services.question_generator._ollama_generate")
    def test_item_format_wins_over_the_requested_split(self, generate):
        # The model returned a true/false item even though only MCQ was asked
        # for. It is still a usable question, so it is kept and labelled by
        # what it actually is rather than by what was requested.
        generate.return_value = _response([TF_ITEM])

        result = qg.generate_questions(CONTENT, "LOT", {"MCQ": 1})

        self.assertEqual(result[0]["format"], "TF")

    @patch("question_generation.services.question_generator._ollama_generate")
    def test_items_with_an_unusable_format_are_dropped(self, generate):
        generate.return_value = _response([{**MCQ_ITEM, "format": "ESSAY"}])

        result = qg.generate_questions(CONTENT, "LOT", {"MCQ": 1})

        self.assertEqual(result, [])

    @patch("question_generation.services.question_generator._ollama_generate")
    def test_requested_split_reaches_the_prompt(self, generate):
        generate.return_value = _response([MCQ_ITEM])

        qg.generate_questions(CONTENT, "LOT", {"MCQ": 2, "TF": 1})

        prompt = generate.call_args.args[0]
        self.assertIn("2", prompt)
        self.assertIn("MCQ", prompt)
        self.assertIn("TF", prompt)


class DistributionConfigTests(SimpleTestCase):
    def test_lot_asks_for_both_formats_in_one_call(self):
        self.assertEqual(set(QUESTION_DISTRIBUTION["LOT"]["format_split"]), {"MCQ", "TF"})

    def test_hot_is_multiple_choice_only(self):
        self.assertEqual(set(QUESTION_DISTRIBUTION["HOT"]["format_split"]), {"MCQ"})

    def test_counts_default_to_three(self):
        self.assertEqual(QUESTION_DISTRIBUTION["LOT"]["count"], 3)
        self.assertEqual(QUESTION_DISTRIBUTION["HOT"]["count"], 3)

    def test_split_sums_to_the_target_count(self):
        for order, config in QUESTION_DISTRIBUTION.items():
            self.assertEqual(
                sum(config["format_split"].values()), config["count"], msg=order
            )

    @override_settings(QUESTION_COUNT_LOT=4, QUESTION_COUNT_HOT=2)
    def test_counts_are_configurable(self):
        counts = thinking_order_counts()
        self.assertEqual(counts["LOT"], 4)
        self.assertEqual(counts["HOT"], 2)
