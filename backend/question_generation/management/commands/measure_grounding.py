"""How grounded a topic's current question bank is.

Run before and after a regeneration to report the effect of a change rather
than assume it. Reads only -- it never writes or deletes a question.
"""

from collections import Counter

from django.core.management.base import BaseCommand

from lessons.models import OutlineNode
from question_generation.models import GeneratedQuestion
from question_generation.services import grounding


class Command(BaseCommand):
    help = "Report how much of a topic's question bank is grounded in its PDFs."

    def add_arguments(self, parser):
        parser.add_argument("outline_node_id", type=int)

    def handle(self, *args, **options):
        node = OutlineNode.objects.get(pk=options["outline_node_id"])
        index = grounding.build_index(node)
        questions = list(
            GeneratedQuestion.objects
            .filter(node__material__outline_node=node, status="final")
            .order_by("id")
        )
        if not questions:
            self.stdout.write("No final questions on this topic.")
            return

        novel_by_question = {q.id: grounding.ungrounded_terms(q, index) for q in questions}
        ungrounded = [q for q in questions if novel_by_question[q.id]]
        orders = Counter(q.thinking_order or "unclassified" for q in questions)
        terms = Counter(
            term for novel in novel_by_question.values() for term in novel
        )

        self.stdout.write(f"topic {node.id}: {node.title}")
        self.stdout.write(f"  index: {len(index.chunks)} passages, searchable={index.searchable}")
        self.stdout.write(f"  questions: {len(questions)}  ({dict(orders)})")
        self.stdout.write(
            f"  ungrounded: {len(ungrounded)}/{len(questions)} "
            f"({100 * len(ungrounded) // len(questions)}%)"
        )
        if terms:
            self.stdout.write(f"  most common out-of-corpus terms: {terms.most_common(10)}")
        for q in ungrounded[:15]:
            self.stdout.write(f"    Q{q.id} {novel_by_question[q.id][:5]} | {q.question_text[:70]}")
