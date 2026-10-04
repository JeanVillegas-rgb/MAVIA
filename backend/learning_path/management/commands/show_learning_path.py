"""Print a topic's learning path, for inspection and defence.

    python manage.py show_learning_path              # list topics with content
    python manage.py show_learning_path 2            # the saved (published) path
    python manage.py show_learning_path 2 --preview  # what publishing would save
    python manage.py show_learning_path 2 --links    # also list prerequisite links
"""

from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from learning_path.models import ConceptPrerequisite
from learning_path.services import build_topic_path, get_published_path
from lessons.models import OutlineNode


class Command(BaseCommand):
    help = "Show a topic's learning path and the prerequisite links behind it."

    def add_arguments(self, parser):
        parser.add_argument("topic_id", nargs="?", type=int, help="Outline topic. Omit to list topics.")
        parser.add_argument("--preview", action="store_true", help="Show the unsaved preview instead of the published path.")
        parser.add_argument("--links", action="store_true", help="Also list every stored prerequisite link.")

    def handle(self, *args, topic_id=None, preview=False, links=False, **options):
        if topic_id is None:
            for node in OutlineNode.objects.filter(learning_object_groups__isnull=False).distinct():
                saved = node.learning_path_steps.count()
                self.stdout.write(f"{node.id:>4}  {node.title}  ({saved} saved steps)")
            return

        node = OutlineNode.objects.filter(pk=topic_id).first()
        if node is None:
            raise CommandError(f"Topic {topic_id} does not exist.")

        if preview:
            path = build_topic_path(node.id)
            self.stdout.write(f"Preview for {node.title} ({path['diagnostics']['ordering']})")
            for step in path["steps"]:
                self.stdout.write(f"  {step['position']:>2}. {step['title']}")
        else:
            path = get_published_path(node)
            if path is None:
                self.stdout.write(f"{node.title} has no published learning path yet.")
            else:
                self.stdout.write(f"{node.title}, published {path['published_at']}")
                titles = {step["concept_id"]: step["title"] for step in path["steps"]}
                for step in path["steps"]:
                    needs = ", ".join(titles[cid] for cid in step["prerequisites"]) or "-"
                    self.stdout.write(f"  {step['position']:>2}. {step['title']}  [after: {needs}]")

        if links:
            # Current concept titles, as the review screen shows them. A group's
            # label is set when the group forms and does not follow renames.
            titles = {step["concept_id"]: step["title"] for step in build_topic_path(node.id)["steps"]}
            rows = ConceptPrerequisite.objects.filter(outline_node=node).select_related("prerequisite", "dependent")
            self.stdout.write(f"Links: {dict(Counter(row.status for row in rows))}")
            for row in rows:
                before = titles.get(row.prerequisite_id, row.prerequisite.label)
                after = titles.get(row.dependent_id, row.dependent.label)
                self.stdout.write(
                    f"  [{row.status:>8}] {before[:34]} -> {after[:34]}"
                    + ("  (cross-section)" if row.cross_section else "")
                )
