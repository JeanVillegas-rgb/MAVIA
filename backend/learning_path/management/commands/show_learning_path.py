"""Print a material's generic learning path, for inspection and defence.

    python manage.py show_learning_path             # list materials
    python manage.py show_learning_path 6           # show the path
    python manage.py show_learning_path 6 --rebuild # re-derive edges first
    python manage.py show_learning_path 6 --edges   # also list every edge
"""

from django.core.management.base import BaseCommand, CommandError

from lessons.models import LearningMaterial
from learning_path.models import PrerequisiteEdge
from learning_path.services import GraphCycleError, build_learning_path


class Command(BaseCommand):
    help = "Show the deterministic learning path derived for a learning material."

    def add_arguments(self, parser):
        parser.add_argument(
            "material_id",
            nargs="?",
            type=int,
            help="Learning material to show. Omit to list the available materials.",
        )
        parser.add_argument(
            "--rebuild",
            action="store_true",
            help="Re-derive the prerequisite edges before building the path.",
        )
        parser.add_argument(
            "--edges",
            action="store_true",
            help="Also print every derived edge with the evidence behind it.",
        )

    def handle(self, *args, **options):
        material_id = options["material_id"]
        if material_id is None:
            self._list_materials()
            return

        try:
            result = build_learning_path(material_id, rebuild=options["rebuild"])
        except LearningMaterial.DoesNotExist:
            raise CommandError(f"No learning material with id {material_id}.")
        except GraphCycleError as error:
            raise CommandError(str(error))

        self._print_path(result)
        if options["edges"]:
            self._print_edges(material_id)

    def _list_materials(self):
        materials = LearningMaterial.objects.select_related("course").order_by("id")
        if not materials:
            self.stdout.write("No learning materials have been uploaded yet.")
            return
        self.stdout.write("Available materials:\n")
        for material in materials:
            count = material.learning_objects.count()
            self.stdout.write(
                f"  id={material.id:<4} {count:>3} objects  "
                f"{material.course.title} / {material.title}"
            )
        self.stdout.write("\nRun: python manage.py show_learning_path <id>")

    def _print_path(self, result):
        diagnostics = result["diagnostics"]
        self.stdout.write("")
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f'Learning path for material {result["material_id"]}: '
                f'{result["material_title"]}'
            )
        )
        self.stdout.write(
            f'  {diagnostics["node_count"]} objects, {diagnostics["edge_count"]} '
            f'prerequisite edges, {diagnostics["root_count"]} starting point(s), '
            f'deepest chain {diagnostics["max_depth"]}'
        )
        if diagnostics["matches_source_order"]:
            self.stdout.write(
                self.style.WARNING(
                    "  NOTE: this path is identical to the PDF's own order - the "
                    "graph found no reason to reorder anything."
                )
            )
        else:
            self.stdout.write(
                f'  {diagnostics["displaced_object_count"]} of '
                f'{diagnostics["node_count"]} objects sit in a different place '
                f"than the PDF had them."
            )

        self.stdout.write("")
        self.stdout.write("  step  depth  pdf#  title")
        self.stdout.write("  " + "-" * 76)
        for step in result["steps"]:
            title = step["title"][:44]
            prerequisites = step["prerequisite_ids"]
            trailer = (
                "start here"
                if not prerequisites
                else f"after {len(prerequisites)} object(s): {prerequisites[:4]}"
            )
            self.stdout.write(
                f'  {step["position"]:>4}  {step["dag_depth"]:>5}  '
                f'{step["source_order"]:>4}  {title:<44} {trailer}'
            )
        self.stdout.write("")

    def _print_edges(self, material_id):
        edges = (
            PrerequisiteEdge.objects.filter(dependent__material_id=material_id)
            .select_related("prerequisite", "dependent")
            .order_by("dependent__order", "prerequisite__order")
        )
        self.stdout.write(self.style.MIGRATE_HEADING("Derived prerequisite edges"))
        if not edges:
            self.stdout.write("  (none)")
            return
        for edge in edges:
            self.stdout.write(
                f'  {edge.prerequisite.title[:28]:<28} -> '
                f'{edge.dependent.title[:28]:<28} [{edge.signal}] {edge.evidence}'
            )
        self.stdout.write("")
