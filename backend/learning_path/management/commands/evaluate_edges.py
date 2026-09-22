"""Diagnostics for the derived prerequisite graph, for finding where it breaks.

    python manage.py evaluate_edges                 # every material, summary
    python manage.py evaluate_edges 6               # one material, full detail
    python manage.py evaluate_edges 6 --rebuild     # re-derive first
    python manage.py evaluate_edges --flag-only     # just the edges worth checking

Read-only. Nothing here changes the database unless --rebuild is passed, which
re-derives edges exactly as the app already does.

What it reports per material:
  * concepts: how many objects own a concept vs None (a None can never be a
    prerequisite, so a lesson full of them is where recall goes to die)
  * graph shape: nodes, edges, density, roots, deepest chain
  * signal mix: which evidence produced the edges (density regressions show here)
  * flagged edges: every reference_asymmetry edge and every explicit_dependency
    edge, printed with the sentence that triggered it, so a person can say
    yes/no and we get a rough precision read
  * suspicious: roots that share distinctive vocabulary with an earlier object
    (possible missed prerequisite) and dependents with many prerequisites
    (possible density / false positives)
"""

import re
from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from lessons.models import LearningMaterial
from learning_path.models import PrerequisiteEdge
from learning_path.services import GraphCycleError, build_learning_path
from learning_path.services.edge_derivation import learning_objects_for_material
from learning_path.services.evidence import build_context
from learning_path.services.concepts import resolve_concepts

MANY_PREREQUISITES = 6


def _ascii(text):
    """PDF text carries curly quotes and dashes the Windows console (cp1252)
    cannot encode. Keep the command usable there."""
    return str(text).encode("ascii", "replace").decode("ascii")


class Command(BaseCommand):
    help = "Report prerequisite-graph diagnostics for one or all materials."

    def add_arguments(self, parser):
        parser.add_argument("material_id", nargs="?", type=int)
        parser.add_argument("--rebuild", action="store_true",
                            help="Re-derive edges before reporting.")
        parser.add_argument("--flag-only", action="store_true",
                            help="Skip the per-material summary; only print flagged edges.")

    def handle(self, *args, **options):
        material_id = options["material_id"]
        if material_id is not None:
            materials = LearningMaterial.objects.filter(pk=material_id)
            if not materials:
                raise CommandError(f"No learning material with id {material_id}.")
        else:
            materials = LearningMaterial.objects.order_by("id")

        totals = Counter()
        for material in materials:
            self._report_material(
                material,
                rebuild=options["rebuild"],
                detail=material_id is not None,
                flag_only=options["flag_only"],
                totals=totals,
            )

        if material_id is None:
            self.stdout.write(self.style.MIGRATE_HEADING("\nAcross all materials"))
            for key in ("objects", "concept_none", "edges",
                        "edges_reference_asymmetry", "edges_explicit", "edges_voted_medium"):
                self.stdout.write(f"  {key:<28} {totals[key]}")

    # ------------------------------------------------------------------ #

    def _report_material(self, material, *, rebuild, detail, flag_only, totals):
        try:
            result = build_learning_path(material.id, rebuild=rebuild)
        except LearningMaterial.DoesNotExist:
            return
        except GraphCycleError as error:
            self.stdout.write(self.style.ERROR(
                f"material {material.id} {material.title}: CYCLE - {error}"
            ))
            return

        objects = learning_objects_for_material(material.id)
        concepts = resolve_concepts(objects)
        concept_none = [o for o in objects if not concepts.get(o.id)]
        diag = result["diagnostics"]
        node_count = diag["node_count"] or 1

        edges = list(
            PrerequisiteEdge.objects.filter(dependent__material_id=material.id)
            .select_related("prerequisite", "dependent")
            .order_by("dependent__order", "prerequisite__order")
        )

        signal_mix = Counter()
        for edge in edges:
            ev = edge.evidence or {}
            if edge.signal == PrerequisiteEdge.Signal.VOTED:
                tier = ev.get("tier", "?")
                types = "+".join(sorted(ev.get(tier, []))) or "?"
                signal_mix[f"voted/{tier}: {types}"] += 1
            else:
                signal_mix[edge.signal] += 1

        totals["objects"] += len(objects)
        totals["concept_none"] += len(concept_none)
        totals["edges"] += len(edges)

        ctx = build_context(material, objects)
        flagged = self._flagged_edges(edges, ctx)
        for kind, rows in flagged.items():
            totals[f"edges_{kind}"] += len(rows)

        if not flag_only:
            self.stdout.write(self.style.MIGRATE_HEADING(
                _ascii(f"\nmaterial {material.id}: {material.title}")
            ))
            self.stdout.write(
                f"  objects {len(objects)}  |  no concept resolved: "
                f"{len(concept_none)} ({100 * len(concept_none) // node_count}%)"
            )
            self.stdout.write(
                f"  graph: {diag['node_count']} nodes, {diag['edge_count']} edges "
                f"(density {diag['edge_count'] / node_count:.1f}/node), "
                f"{diag['root_count']} roots, deepest chain {diag['max_depth']}, "
                f"{'== source order' if diag['matches_source_order'] else 'reordered'}"
            )
            self.stdout.write("  signal mix:")
            for label, count in signal_mix.most_common():
                self.stdout.write(f"    {count:>4}  {label}")

            if detail and concept_none:
                self.stdout.write("  objects that own no concept (cannot be a prerequisite):")
                for obj in concept_none[:20]:
                    self.stdout.write(_ascii(f"    #{obj.order:<3} {obj.title[:70]}"))

            suspicious = self._suspicious(objects, edges, ctx)
            if suspicious:
                self.stdout.write("  worth a look:")
                for line in suspicious:
                    self.stdout.write(_ascii(f"    {line}"))

        for kind, rows in flagged.items():
            if not rows:
                continue
            self.stdout.write(self.style.WARNING(
                f"  flagged [{kind}] ({len(rows)}) - is each a real prerequisite?"
            ))
            for prereq_title, dep_title, sentence in rows:
                self.stdout.write(_ascii(f"    {prereq_title[:34]:<34} -> {dep_title[:34]}"))
                if sentence:
                    self.stdout.write(_ascii(f'        "{sentence[:150]}"'))

    # ------------------------------------------------------------------ #

    def _flagged_edges(self, edges, ctx):
        out = {"reference_asymmetry": [], "explicit": [], "voted_medium": []}
        for edge in edges:
            ev = edge.evidence or {}
            if edge.signal != PrerequisiteEdge.Signal.VOTED:
                continue
            strong = set(ev.get("strong", []))
            medium = set(ev.get("medium", []))
            row = (
                edge.prerequisite.title,
                edge.dependent.title,
                self._trigger_sentence(edge, ctx),
            )
            if "reference_asymmetry" in medium:
                out["reference_asymmetry"].append(row)
            if "explicit_dependency" in strong:
                out["explicit"].append(row)
            if not strong and medium:
                out["voted_medium"].append(row)
        return out

    def _trigger_sentence(self, edge, ctx):
        """The dependent sentence that best explains the edge, if we can find it."""
        concept = ctx.concepts.get(edge.prerequisite_id)
        if not concept:
            return ""
        pattern = re.compile(re.escape(concept), re.I)
        for sentence in re.split(r"(?<=[.!?])\s+", edge.dependent.content or ""):
            if pattern.search(sentence):
                return sentence.strip()
        return ""

    def _suspicious(self, objects, edges, ctx):
        lines = []
        has_prereq = {e.dependent_id for e in edges}
        prereq_count = Counter(e.dependent_id for e in edges)
        positions = ctx.positions

        for obj in objects:
            if obj.id in has_prereq:
                continue
            if positions[obj.id] == 0:
                continue
            for earlier in objects:
                if positions[earlier.id] >= positions[obj.id]:
                    continue
                shared = {
                    t for t in ctx.terms[earlier.id] & ctx.terms[obj.id]
                    if ctx.frequencies[t] <= ctx.distinctive_limit
                }
                if len(shared) >= 3:
                    lines.append(
                        f"root '{obj.title[:34]}' shares {sorted(shared)[:4]} "
                        f"with earlier '{earlier.title[:34]}' - missed prerequisite?"
                    )
                    break

        for dep_id, count in prereq_count.items():
            if count >= MANY_PREREQUISITES:
                title = ctx.by_id[dep_id].title if dep_id in ctx.by_id else str(dep_id)
                lines.append(f"'{title[:40]}' has {count} prerequisites - density / false positives?")
        return lines
