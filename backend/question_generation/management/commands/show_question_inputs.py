"""Print what question generation would send for a topic: show_question_inputs <topic> [--prompt]."""

import textwrap

from django.core.management.base import BaseCommand, CommandError

from lessons.models import LearningObjectGroup, OutlineNode
from lessons.services.question_workflow import concept_tier_counts
from question_generation.services.pipeline import (
    QUESTION_DISTRIBUTION, concept_name, concept_source_text, question_minimum,
)
from question_generation.services.question_generator import _build_prompt
from question_generation.views import _standard_question_source


class Command(BaseCommand):
    help = "Show each concept's reference content and what the Generate button would ask the LLM for. Calls no LLM, saves nothing."

    def add_arguments(self, parser):
        parser.add_argument("topic_id", type=int)
        parser.add_argument(
            "--prompt", action="store_true",
            help="Also print the full prompt for each tier a concept still needs, or for both tiers when it needs none (what Regenerate sends).",
        )

    def handle(self, *args, topic_id, prompt=False, **options):
        topic = OutlineNode.objects.filter(pk=topic_id).first()
        if topic is None:
            raise CommandError(f"Topic {topic_id} does not exist.")
        minimum = question_minimum()
        goal = " + ".join(f"{minimum[order]} {order}" for order in QUESTION_DISTRIBUTION)
        sources = []
        for group in LearningObjectGroup.objects.filter(outline_node=topic).order_by("id"):
            member = group.learning_objects.filter(
                material__generated_json__learning_objects_confirmed=True,
            ).exclude(content="").order_by("material_id", "order", "id").first()
            if member is None:
                continue
            standard, error = _standard_question_source(member)
            if error:
                self.stdout.write(f"SKIPPED {group.label!r}: {error}")
                continue
            sources.append(standard)

        short = [node for node in sources if any(concept_tier_counts(node)[order] < minimum[order] for order in QUESTION_DISTRIBUTION)]
        self.stdout.write(f"TOPIC: {topic.title}  ({len(sources)} concepts)")
        self.stdout.write(f"CONCEPTS SHORT OF {goal}: {', '.join(concept_name(node) for node in short) or 'none'}")
        for position, node in enumerate(sources, start=1):
            have = concept_tier_counts(node)
            wanted = [order for order in QUESTION_DISTRIBUTION if have[order] < minimum[order]]
            content = concept_source_text(node)
            self.stdout.write("")
            self.stdout.write(
                f"[{position}/{len(sources)}] CONCEPT: {concept_name(node)}  (object {node.id})  "
                f"has {' / '.join(f'{have[order]} {order}' for order in QUESTION_DISTRIBUTION)}  -> "
                + (f"Generate would write {' and '.join(wanted)}" if wanted else "meets the minimum")
            )
            self.stdout.write("REFERENCE CONTENT FOR THE QUESTIONS:")
            self.stdout.write(textwrap.indent(content, "    "))
            if prompt:
                for order in wanted or list(QUESTION_DISTRIBUTION):
                    split = QUESTION_DISTRIBUTION[order]["format_split"]
                    self.stdout.write(f"PROMPT ({order}, {' + '.join(f'{n} {fmt}' for fmt, n in split.items())}):")
                    self.stdout.write(textwrap.indent(_build_prompt(content, order, split), "    "))
