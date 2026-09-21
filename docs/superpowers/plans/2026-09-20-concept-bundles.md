# Concept Bundles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the manual merge step with concepts that hold an ordered bundle of objects per PDF, formed automatically when another PDF corroborates them.

**Architecture:** A bundle is derived, never stored: `bundles_for_group()` returns `{material_id: [objects in document order]}`, and every consumer reads bundles only through it. Grouping drops the one-object-per-PDF rule and places corroborated section runs into the counterpart's concept. Version roles move from per-object `LessonVariant` rows to a per-bundle assignment on the group; only generated text stays in `LessonVariant`, one row per object of the Normal bundle.

**Tech Stack:** Django 5.2, Django REST Framework, SQLite, sentence-transformers (all-MiniLM-L6-v2 + cross-encoder/stsb-roberta-base), Ollama/Gemma for generation, React 18 + Vite.

**Spec:** `docs/superpowers/specs/2026-09-20-concept-bundles-design.md`

## Global Constraints

- A run under one heading is **not** joined into one object; objects stay and the concept holds them as an ordered bundle.
- Bundles form automatically only when another PDF corroborates them; structure alone must never fuse Matter, Solid, Liquid and Gas (all under the "Matter" heading in `Lesson-1_Solid-Liquid-and-Gas`).
- Scores at or above the auto threshold (0.6) with the configured margin apply automatically; between the review threshold (0.3) and 0.6 a suggestion card is raised.
- Versions are classified **per bundle**; missing versions are generated **per object** of the Normal bundle.
- Teacher controls are plain buttons in the existing Review Connections step: Move out, Move to…, Up, Down. No drag-and-drop.
- No migration of merged rows: `merged_from` and the merge service are deleted; the user re-uploads.
- Learning-path criteria, the vote, ordering, gold fixtures and `test_gold_paths.py` stay unchanged. Expected gold result after this work: topic 62 10/10, topic 79 4/8 with the four `known_missing` edges, 0 forbidden, both orders matching.
- Never stage `backend/course/tests.py`, `backend/course/variant_generator.py`, `MAVIA MANUSCRIPT.docx`, `frontend/dist/*`; always `git add` explicit paths.
- Backend commands run from `C:\MAVIA\backend` with system `python` (no venv); frontend builds from `C:\MAVIA\frontend` with `npm run build`. Shell is Git Bash on Windows.
- Every commit message ends with a blank line then `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Rollback branch: `origin/current-with-merge-function`.

## File Structure

| File | Responsibility |
|---|---|
| `backend/lessons/services/concept_bundles.py` (new) | Derive bundles: `bundles_for_group`, `bundle_text`, `bundle_lead`, `bundle_heading`, `material_order` |
| `backend/lessons/services/semantic_grouping.py` | Drop the one-object-per-PDF eligibility rule |
| `backend/lessons/services/unit_matching.py` | Detect runs; place corroborated runs into a concept; raise cards for uncertain ones (no merging) |
| `backend/course/version_assignment.py` | Per-bundle roles on the group; generated rows per Normal-bundle object |
| `backend/course/variant_generator.py` | `fill_missing_slots` called per object of the Normal bundle |
| `backend/lessons/views.py` | Bundle-shaped versions payload; Move out / Move to / reorder endpoints; merge and split endpoints deleted |
| `backend/lessons/serializers.py` | Bundles in the learning-resources payload |
| `backend/course/services.py`, `backend/lessons/services/lesson_package.py` | Assemble a version from an ordered bundle |
| `backend/question_generation/services/pipeline.py` | Questions generated from the Normal bundle text |
| `backend/learning_path/services/concept_units.py` | `member_text` in bundle order; concept name from the bundle's first heading |
| `frontend/src/pages/TopicDetailPage.jsx`, `frontend/src/api.js`, `frontend/src/styles/pipeline.css`, `frontend/src/index.css` | Bundle columns, object controls; merge/split UI removed |
| Deleted | `backend/lessons/services/object_merge.py`, `backend/lessons/test_object_merge.py`, `LearningObject.merged_from` |

Build order: 1 helper → 2 grouping rule → 3 placement → 4 version roles → 5 generation → 6 API and UI → 7 learning path → 8 removals → 9 consumers → 9b questions → 10 verification.

---

### Task 1: The bundle helper

**Files:**
- Create: `backend/lessons/services/concept_bundles.py`
- Test: `backend/lessons/test_concept_bundles.py`

**Interfaces:**
- Produces:
  - `material_order(node) -> list[int]` — the topic's material ids, oldest upload first.
  - `bundles_for_group(group) -> dict[int, list[LearningObject]]` — objects per material, each list ordered by `(order, id)`; materials keyed by id.
  - `ordered_members(group) -> list[LearningObject]` — every member, materials in upload order, objects in document order.
  - `bundle_text(objects) -> str` — the objects' `content` joined with `"\n"`, skipping empties.
  - `bundle_lead(objects) -> LearningObject` — the first object.
  - `bundle_heading(objects) -> str` — the first non-empty `section_title`, else the first object's `title`.

- [ ] **Step 1: Write the failing test**

Create `backend/lessons/test_concept_bundles.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python manage.py test lessons.test_concept_bundles -v 2`
Expected: ERROR, `No module named 'lessons.services.concept_bundles'`.

- [ ] **Step 3: Implement**

Create `backend/lessons/services/concept_bundles.py`:

```python
"""A concept's objects from one PDF, in document order.

Two PDFs chunk the same lesson at different grain: one teaches Solid in a
single passage, the other as a section plus a diagram plus examples. A concept
therefore holds a *bundle* per PDF rather than a single object. Nothing is
stored: a bundle is derived here, and every consumer -- version assignment,
audio, questions, the learning path, the API -- reads it through this module so
their ordering can never drift apart.
"""

from collections import defaultdict


def material_order(node):
    """The topic's material ids, oldest upload first."""
    return list(
        node.materials.order_by("created_at", "id").values_list("id", flat=True)
    )


def bundles_for_group(group):
    """``{material_id: [objects in document order]}`` for one concept."""
    bundles = defaultdict(list)
    for item in group.learning_objects.select_related("material").all():
        bundles[item.material_id].append(item)
    for objects in bundles.values():
        objects.sort(key=lambda item: (item.order, item.id))
    return dict(bundles)


def ordered_members(group):
    """Every member: materials in upload order, objects in document order."""
    bundles = bundles_for_group(group)
    node = group.outline_node
    ranked = {material_id: rank for rank, material_id in enumerate(material_order(node))}
    members = []
    for material_id in sorted(bundles, key=lambda mid: (ranked.get(mid, len(ranked)), mid)):
        members.extend(bundles[material_id])
    return members


def bundle_text(objects):
    """The bundle's text: each object's content, in order."""
    return "\n".join(
        (item.content or "").strip() for item in objects if (item.content or "").strip()
    )


def bundle_lead(objects):
    """The object that speaks for the bundle -- its first."""
    return objects[0] if objects else None


def bundle_heading(objects):
    """What the bundle is called: its first heading, else its first title.

    Taken from the heading rather than the first object because a bundle often
    opens with a figure that has no heading of its own; naming the concept
    after that figure cost the learning path its edges when merging did it.
    """
    for item in objects:
        if (item.section_title or "").strip():
            return item.section_title.strip()
    return (objects[0].title or "").strip() if objects else ""
```

- [ ] **Step 4: Run to verify it passes**

Run: `python manage.py test lessons.test_concept_bundles -v 2`
Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add lessons/services/concept_bundles.py lessons/test_concept_bundles.py
git commit -m "Derive a concept's per-PDF object bundles

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Let a concept hold several objects from one PDF

**Files:**
- Modify: `backend/lessons/services/semantic_grouping.py` (`semantic_decision`, the `eligible_groups` comprehension)
- Test: `backend/lessons/test_semantic_grouping.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: `semantic_decision` may return a group that already contains an object from the source's own material.

- [ ] **Step 1: Write the failing test**

Append to `backend/lessons/test_semantic_grouping.py` (it already defines `FakeRuntime`; follow the file's existing fixture style and imports):

```python
class SameMaterialEligibilityTests(TestCase):
    """A concept may hold several objects from one PDF.

    One file teaches Solid as a section plus a diagram; the other as a single
    passage. Refusing a group that already holds an object from this material
    is what used to make that impossible.
    """

    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="States")
        confirmed = {"learning_objects_confirmed": True}
        self.first = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="A", generated_json=dict(confirmed))
        self.second = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="B", generated_json=dict(confirmed))
        self.group = LearningObjectGroup.objects.create(outline_node=self.topic, label="Solid")
        self.solid = LearningObject.objects.create(
            material=self.first, group=self.group, title="Solid",
            content="A solid keeps its shape.", order=0)
        self.solids = LearningObject.objects.create(
            material=self.second, group=self.group, title="Solids",
            content="In a solid, particles are packed tightly.", order=0)
        self.diagram = LearningObject.objects.create(
            material=self.second, group=LearningObjectGroup.objects.create(outline_node=self.topic),
            title="Diagram description", content="Particles drawn in a grid.",
            section_title="Solids", order=1)

    def test_a_group_holding_this_material_is_still_eligible(self):
        with patch.dict(os.environ, SEMANTIC_ENV):
            decision = semantic.semantic_decision(
                self.second, self.diagram.title, self.diagram.content, self.diagram.kind,
                self.diagram.order, section_title=self.diagram.section_title,
                source_object_id=self.diagram.id, runtime_instance=FakeRuntime(),
            )

        self.assertIsNotNone(decision)
        self.assertEqual(decision["candidate"].group_id, self.group.id)
```

If `semantic_decision` has no `runtime_instance` parameter, patch `semantic.runtime` with `FakeRuntime()` the way the surrounding tests do, and drop that argument.

- [ ] **Step 2: Run to verify it fails**

Run: `python manage.py test lessons.test_semantic_grouping.SameMaterialEligibilityTests -v 2`
Expected: FAIL — `decision` is `None`, because the group is filtered out.

- [ ] **Step 3: Implement**

In `semantic_decision`, the eligibility comprehension currently reads:

```python
    eligible_groups = {
        group_id for group_id, rows in members.items()
        if all(item.material_id != material.id and item.kind == kind
               and (item.material.generated_json or {}).get("learning_objects_confirmed") for item in rows)
    }
```

Replace it with:

```python
    # A concept may hold several objects from one PDF (a section, its diagram
    # and its examples), so a group is no longer disqualified for already
    # holding one of this material's objects. It must still teach the same
    # kind of content and come from confirmed files.
    eligible_groups = {
        group_id for group_id, rows in members.items()
        if any(item.material_id != material.id for item in rows)
        and all(item.kind == kind
                and (item.material.generated_json or {}).get("learning_objects_confirmed")
                for item in rows)
    }
```

- [ ] **Step 4: Run the grouping suite**

Run: `python manage.py test lessons.test_semantic_grouping lessons.test_label_corroboration lessons.test_mutual_match -v 2`
Expected: the new test passes. If an existing test asserted that a same-material group is refused, that is the behaviour being replaced: update it with a comment `# Changed 2026-09-20: a concept may hold several objects per PDF.` and list it in your report. Any other failure is a regression to fix in the code.

- [ ] **Step 5: Commit**

```bash
git add lessons/services/semantic_grouping.py lessons/test_semantic_grouping.py
git commit -m "Allow a concept to hold several objects from one PDF

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Place corroborated runs into a concept

**Files:**
- Modify: `backend/lessons/services/unit_matching.py`
- Modify: `backend/lessons/views.py` (`accept_match_suggestion`: place, do not merge)
- Test: `backend/lessons/test_unit_matching.py`

**Interfaces:**
- Consumes: `concept_bundles.bundle_text`, `bundle_lead`; `unit_matching.find_units`, `heading_key`, `heading_unit_candidates` (existing).
- Produces:
  - `place_unit(objects, target_group) -> None` — moves every object into `target_group`, clearing `represented_by` and refreshing grouping fingerprints.
  - `refresh_heading_unit_suggestions(node, runtime_instance=None) -> dict` — now returns `{"placed": n, "pending": n}`: it *applies* matches at or above the auto threshold and raises cards for the rest.

- [ ] **Step 1: Write the failing test**

In `backend/lessons/test_unit_matching.py`, keep the existing `UnitFixture`, `HeadingKeyTests`, `FindUnitTests` and `CandidateTests`. Replace the suggestion tests with:

```python
class PlacementTests(UnitFixture):
    def _refresh(self, score):
        runtime = FakeRuntime()
        runtime.pair_scores = lambda pairs: [score for _ in pairs]
        with patch.dict(os.environ, SEMANTIC_ENV):
            return refresh_heading_unit_suggestions(self.topic, runtime_instance=runtime)

    def test_a_confident_match_is_placed_without_a_card(self):
        counts = self._refresh(0.8)

        self.solids_b.refresh_from_db()
        self.diagram_b.refresh_from_db()
        self.solid_a.refresh_from_db()
        self.assertEqual(self.solids_b.group_id, self.solid_a.group_id)
        self.assertEqual(self.diagram_b.group_id, self.solid_a.group_id)
        self.assertGreaterEqual(counts["placed"], 1)
        self.assertFalse(
            LearningObjectMatchSuggestion.objects.filter(
                status=LearningObjectMatchSuggestion.Status.PENDING,
                evidence__label=heading_key("Solids"),
            ).exists()
        )

    def test_an_uncertain_match_raises_a_card_and_places_nothing(self):
        self._refresh(0.45)

        self.solids_b.refresh_from_db()
        self.assertNotEqual(self.solids_b.group_id, self.solid_a.group_id)
        self.assertTrue(
            LearningObjectMatchSuggestion.objects.filter(
                status=LearningObjectMatchSuggestion.Status.PENDING,
            ).exists()
        )

    def test_a_score_below_the_review_threshold_does_nothing(self):
        self._refresh(0.1)

        self.solids_b.refresh_from_db()
        self.assertNotEqual(self.solids_b.group_id, self.solid_a.group_id)
        self.assertFalse(LearningObjectMatchSuggestion.objects.exists())

    def test_a_section_of_separate_concepts_is_never_placed(self):
        """Regression: the "Matter" heading covers Matter, Solid and Liquid in
        material A; no PDF names that run, so it must not fuse."""
        self._refresh(0.8)

        self.matter.refresh_from_db()
        self.solid_a.refresh_from_db()
        self.assertNotEqual(self.matter.group_id, self.solid_a.group_id)

    def test_accepting_a_card_places_the_objects_without_merging(self):
        self._refresh(0.45)
        suggestion = LearningObjectMatchSuggestion.objects.filter(
            status=LearningObjectMatchSuggestion.Status.PENDING).first()
        client = authenticated_api_client()

        response = client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.topic.id}"
            f"/match-suggestions/{suggestion.id}/accept/",
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        members = {
            item.id for item in LearningObject.objects.filter(
                group_id=LearningObject.objects.get(pk=suggestion.source_learning_object_id).group_id)
        }
        expected = {
            suggestion.source_learning_object_id, suggestion.candidate_learning_object_id,
            *suggestion.source_extra_ids, *suggestion.candidate_extra_ids,
        }
        self.assertEqual(members & expected, expected)
        self.assertEqual(LearningObject.objects.filter(pk__in=expected).count(), len(expected))
```

- [ ] **Step 2: Run to verify it fails**

Run: `python manage.py test lessons.test_unit_matching -v 2`
Expected: FAIL — `refresh_heading_unit_suggestions` returns an int and never places anything.

- [ ] **Step 3: Implement placement**

In `backend/lessons/services/unit_matching.py`:

- Replace the `object_merge` import with `from .concept_bundles import bundle_text` and drop `choose_kept_row`/`merge_text` usage (use `bundle_text(objects)` in the scoring call).
- Add:

```python
def place_unit(objects, target_group):
    """Move every object of a unit into one concept.

    Placement replaces the old merge: the objects keep their own text and
    order, and the concept simply holds them all.
    """
    from ..models import LearningObject, LearningObjectGroup

    emptied = {item.group_id for item in objects if item.group_id and item.group_id != target_group.id}
    for item in objects:
        if item.group_id == target_group.id:
            continue
        item.group = target_group
        item.represented_by = None
        item.mark_grouping_current()
        item.save(update_fields=["group", "represented_by", "grouping_content_hash"])
    LearningObjectGroup.objects.filter(
        pk__in=emptied, learning_objects__isnull=True,
    ).delete()
```

- In `refresh_heading_unit_suggestions`, after computing `score` for a candidate, branch on confidence instead of always writing a suggestion:

```python
        config = semantic_grouping.policy()
        auto = config["auto_threshold"]
        confident = (
            semantic_grouping.mode() == "auto"
            and auto is not None
            and score >= auto
        )
        if confident:
            target = next(
                (item.group for item in [*left, *right] if item.group_id), None,
            ) or LearningObjectGroup.objects.create(
                outline_node=node, label=(left[0].title or "")[:255],
            )
            place_unit([*left, *right], target)
            placed += 1
            continue
```

Keep the existing pending-suggestion path (including `_resolve_unit_pairing`, which must still never touch a non-pending row) for scores between the review threshold and `auto`. Return `{"placed": placed, "pending": pending}`.

- [ ] **Step 4: Accept a card by placing**

In `backend/lessons/views.py`, `accept_match_suggestion`: replace the call to `self._merge_suggestion_units(...)` with placement. Read the current function first; the new body of that branch is:

```python
        if suggestion.source_extra_ids or suggestion.candidate_extra_ids:
            from .services.unit_matching import place_unit

            objects = list(
                LearningObject.objects.filter(
                    pk__in=[
                        suggestion.source_learning_object_id,
                        suggestion.candidate_learning_object_id,
                        *suggestion.source_extra_ids,
                        *suggestion.candidate_extra_ids,
                    ],
                    material__outline_node=node,
                )
            )
            expected = 2 + len(suggestion.source_extra_ids) + len(suggestion.candidate_extra_ids)
            if len(objects) != expected:
                return Response(
                    {"detail": "This suggestion is out of date. Refresh the page and review it again."},
                    status=status.HTTP_409_CONFLICT,
                )
            place_unit(objects, source.group or candidate.group or LearningObjectGroup.objects.create(
                outline_node=node, label=(source.title or "")[:255],
            ))
```

Delete `_merge_suggestion_units` entirely. The rest of `accept_match_suggestion` (status, snapshots, `record_teacher_match_decision`) stays, and the whole state-changing part remains inside its `transaction.atomic()` block.

- [ ] **Step 5: Run the tests**

Run: `python manage.py test lessons.test_unit_matching -v 2`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add lessons/services/unit_matching.py lessons/views.py lessons/test_unit_matching.py
git commit -m "Place corroborated runs into a concept instead of merging them

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Version roles per bundle

**Files:**
- Modify: `backend/course/version_assignment.py`
- Test: `backend/course/test_version_assignment.py` (existing file; add a bundle test class)

**Read first:** `assign_group_versions`, `_store`, `settle_group`, `group_original_id`, `assign_source_to_slot`, `release_from_group`. They assume one object per PDF throughout.

**Interfaces:**
- Consumes: `lessons.services.concept_bundles.bundles_for_group`, `bundle_text`, `bundle_lead`, `material_order`.
- Produces (in `course.version_assignment`):
  - `normal_material_id(group) -> int | None`
  - `bundle_roles(group) -> dict[int, str]` — `{material_id: "SIMPLIFIED" | "ELABORATED" | "EXTRA"}` for every non-Normal bundle.
  - `set_bundle_role(group, material_id, role) -> None` — teacher override; writes `assigned_by="teacher"` provenance into `version_selection`.
  - `version_bundles(group) -> dict[str, list[LearningObject]]` — `{"NORMAL": [...], "SIMPLIFIED": [...], ...}` for roles supplied by a PDF.
  - `assign_group_versions(group, *, use_llm=False) -> dict` — unchanged return keys (`representative_id`, `original_selected`, `classification_complete`, `assigned`, `needs_confirmation`, `extras`, `classification_error`) plus `normal_material_id` and `bundle_roles`. `representative_id` is now the **lead object of the Normal bundle**.

**Storage:** `group.version_selection` gains `normal_material_id`, `bundle_roles` (`{"18": "SIMPLIFIED"}`), `bundle_roles_assigned_by` (`{"18": "teacher" | "heuristic" | "llm_validated"}`) and `roles_signature` (sha256 over each bundle's material id and text). `label_locked` keeps its meaning. `LessonVariant` rows with `origin=source_pdf` are no longer created: a PDF-supplied version is its own objects.

- [ ] **Step 1: Write the failing test**

Append to `backend/course/test_version_assignment.py`:

```python
class BundleRoleTests(TestCase):
    """Roles are decided per bundle, not per object.

    One PDF teaches Solid as a section, its diagram and its examples; the other
    as a single passage. The whole bundle takes one role.
    """

    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="States")
        confirmed = {"learning_objects_confirmed": True}
        self.first = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="A", generated_json=dict(confirmed))
        self.second = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="B", generated_json=dict(confirmed))
        self.group = LearningObjectGroup.objects.create(outline_node=self.topic, label="Solid")
        self.normal = LearningObject.objects.create(
            material=self.first, group=self.group, title="Solid", order=0,
            content=(
                "A solid has a definite shape and a definite volume because its constituent "
                "particles occupy fixed positions within a rigid lattice arrangement."
            ),
        )
        self.simple_lead = LearningObject.objects.create(
            material=self.second, group=self.group, title="Solids", order=0, section_title="Solids",
            content="In a solid, bits are packed tight. They stay in place.",
        )
        self.simple_tail = LearningObject.objects.create(
            material=self.second, group=self.group, title="Everyday examples", order=1,
            section_title="Solids", content="Ice cubes. A rock. A book.",
        )

    def test_the_whole_bundle_takes_one_role(self):
        outcome = assign_group_versions(self.group)

        self.assertEqual(outcome["normal_material_id"], self.first.id)
        self.assertEqual(outcome["representative_id"], self.normal.id)
        self.assertEqual(outcome["bundle_roles"], {self.second.id: "SIMPLIFIED"})

    def test_a_pdf_supplied_version_stores_no_copied_text(self):
        assign_group_versions(self.group)

        self.assertFalse(
            LessonVariant.objects.filter(origin=LessonVariant.Origin.SOURCE_PDF).exists()
        )
        self.assertEqual(
            version_bundles(self.group)["SIMPLIFIED"], [self.simple_lead, self.simple_tail],
        )

    def test_a_teacher_role_change_survives_a_refresh(self):
        assign_group_versions(self.group)

        set_bundle_role(self.group, self.second.id, "EXTRA")
        outcome = assign_group_versions(self.group)

        self.assertEqual(outcome["bundle_roles"], {self.second.id: "EXTRA"})

    def test_the_normal_bundle_is_reported_in_document_order(self):
        extra = LearningObject.objects.create(
            material=self.first, group=self.group, title="Particle diagram", order=1,
            section_title="Solid", content="Particles sit in a grid.",
        )

        self.assertEqual(version_bundles(self.group)["NORMAL"], [self.normal, extra])
```

Add `version_bundles` and `set_bundle_role` to the module's imports at the top of the test file.

- [ ] **Step 2: Run to verify it fails**

Run: `python manage.py test course.test_version_assignment.BundleRoleTests -v 2`
Expected: ImportError for `version_bundles` / `set_bundle_role`.

- [ ] **Step 3: Implement**

In `backend/course/version_assignment.py`:

- Import the helper: `from lessons.services.concept_bundles import bundle_lead, bundle_text, bundles_for_group, material_order`.
- Add:

```python
def normal_material_id(group):
    """Which PDF supplies this concept's Normal version, if one is decided."""
    selection = group.version_selection or {}
    stored = selection.get("normal_material_id")
    bundles = bundles_for_group(group)
    if stored in bundles:
        return stored
    order = [mid for mid in material_order(group.outline_node) if mid in bundles]
    return order[0] if order else None


def bundle_roles(group):
    """``{material_id: role}`` for every bundle other than Normal."""
    selection = group.version_selection or {}
    stored = {int(key): value for key, value in (selection.get("bundle_roles") or {}).items()}
    return {
        material_id: role
        for material_id, role in stored.items()
        if material_id in bundles_for_group(group) and material_id != normal_material_id(group)
    }


def set_bundle_role(group, material_id, role):
    """Record the teacher's role for one bundle."""
    if role not in (*PRIMARY_SLOTS, "EXTRA"):
        raise ValueError("Unknown version slot")
    selection = dict(group.version_selection or {})
    roles = dict(selection.get("bundle_roles") or {})
    provenance = dict(selection.get("bundle_roles_assigned_by") or {})
    roles[str(material_id)] = role
    provenance[str(material_id)] = LessonVariant.AssignedBy.TEACHER
    selection["bundle_roles"] = roles
    selection["bundle_roles_assigned_by"] = provenance
    group.version_selection = selection
    group.save(update_fields=["version_selection"])


def version_bundles(group):
    """``{role: [objects]}`` for the versions a PDF supplies."""
    bundles = bundles_for_group(group)
    normal_id = normal_material_id(group)
    result = {}
    if normal_id in bundles:
        result["NORMAL"] = bundles[normal_id]
    for material_id, role in bundle_roles(group).items():
        result.setdefault(role, bundles[material_id])
    return result
```

- Rework `assign_group_versions` to decide roles per bundle:
  - members are now bundles: `bundles = bundles_for_group(group)`; fewer than two bundles → the existing early return, with `representative_id` set to the lead of the single bundle.
  - the Normal bundle is `normal_material_id(group)`; `representative = bundle_lead(bundles[normal_id])`.
  - for each other bundle, `verdict = compare(bundle_text(normal_bundle), bundle_text(other_bundle))`, and when `use_llm` is set, classification runs on lightweight proxies carrying the bundle's combined text — build them with `SimpleNamespace(id=bundle_lead(objects).id, content=bundle_text(objects), title=bundle_heading(objects))` so `classify_group_versions` keeps working unchanged.
  - a role whose provenance is `teacher` is never overwritten.
  - store `normal_material_id`, `bundle_roles`, `bundle_roles_assigned_by`, `roles_signature` in `version_selection`; keep `label_locked`.
  - **stop calling `_store` for PDF-supplied roles.** Delete `_store`'s source-PDF path and the `stored_source_rows` / `stored_by_source` machinery that reads it. `LessonVariant` now only holds generated rows.
  - `assigned` / `needs_confirmation` / `extras` keep their shapes, but each entry describes a **bundle**: `learning_object_id` is the bundle's lead id, plus a new `material_id` key.
  - keep `_sync_automatic_group_label(group, representative, members=...)`, and pass the Normal bundle's heading so an unlocked label follows `bundle_heading(normal_bundle)`.
- `group_original_id` returns the lead of the Normal bundle; `assign_source_to_slot(representative, source, slot)` becomes a thin wrapper that calls `set_bundle_role(group, source.material_id, slot)`; `release_from_group` no longer has source rows to undo, so it only clears `represented_by` on the leaving object and resets the stored roles for its material.
- In `settle_group`, mark every object of a non-Normal bundle `represented_by` the Normal lead (replacing the per-object `placed_ids` loop).

- [ ] **Step 4: Run the course suite**

Run: `python manage.py test course -v 2`
Expected: the new class passes. Existing tests that assert `LessonVariant` rows with `origin=source_pdf`, or per-object role assignment, encode the replaced behaviour: update them with `# Changed 2026-09-20: roles are per bundle; a PDF-supplied version is its own objects.` and list each in your report. Anything else is a regression to fix in the code.

- [ ] **Step 5: Commit**

```bash
git add course/version_assignment.py course/test_version_assignment.py
git commit -m "Assign version roles per bundle instead of per object

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Generate missing versions per object

**Files:**
- Modify: `backend/course/variant_generator.py` (add a bundle-level entry point; `fill_missing_slots` itself stays per object)
- Modify: `backend/course/version_assignment.py` (`settle_group` calls the new entry point)
- Test: `backend/course/test_bulk_version_generation.py` (append) or a new `backend/course/test_bundle_generation.py`

**Interfaces:**
- Consumes: `version_bundles(group)`, `fill_missing_slots(learning_object, target_slots=None, *, replace_stale=False)`.
- Produces: `fill_missing_bundle_slots(group, target_slots=None, *, replace_stale=False) -> {"generated": [...], "skipped": [...], "errors": [...]}` — runs `fill_missing_slots` for each object of the Normal bundle, in order, and only for roles no bundle supplies.

- [ ] **Step 1: Write the failing test**

```python
class BundleGenerationTests(TestCase):
    """Missing versions are written one object at a time.

    A long bundle in one call is where Gemma returns invalid JSON; per object
    the calls stay short and one failure costs one object, not a concept.
    """

    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="States")
        confirmed = {"learning_objects_confirmed": True}
        self.first = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="A", generated_json=dict(confirmed))
        self.second = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="B", generated_json=dict(confirmed))
        self.group = LearningObjectGroup.objects.create(outline_node=self.topic, label="Solid")
        self.normal = LearningObject.objects.create(
            material=self.first, group=self.group, title="Solid", order=0,
            content=(
                "A solid has a definite shape and a definite volume because its constituent "
                "particles occupy fixed positions within a rigid lattice arrangement."
            ),
        )
        self.normal_tail = LearningObject.objects.create(
            material=self.first, group=self.group, title="Particle diagram", order=1,
            section_title="Solid", content="Particles sit in a grid and vibrate in place.",
        )
        self.simple = LearningObject.objects.create(
            material=self.second, group=self.group, title="Solids", order=0, section_title="Solids",
            content="In a solid, bits are packed tight. They stay in place.",
        )
        assign_group_versions(self.group)  # SIMPLIFIED comes from the second PDF

    def test_each_normal_object_gets_its_own_generated_row(self):
        with patch("course.variant_generator._request_variants") as request:
            request.return_value = {"SIMPLIFIED": "Short.", "ELABORATED": "Longer text."}
            outcome = fill_missing_bundle_slots(self.group)

        self.assertEqual(request.call_count, 2)
        rows = LessonVariant.objects.filter(
            learning_object__in=[self.normal, self.normal_tail], variant="ELABORATED",
        ).order_by("learning_object__order")
        self.assertEqual([row.learning_object_id for row in rows], [self.normal.id, self.normal_tail.id])
        self.assertEqual(outcome["errors"], [])

    def test_a_failure_on_one_object_leaves_the_others(self):
        def flaky(learning_object, model):
            if learning_object.id == self.normal.id:
                raise VariantGenerationError("Gemma did not return valid JSON.")
            return {"SIMPLIFIED": "Short.", "ELABORATED": "Longer text."}

        with patch("course.variant_generator._request_variants", side_effect=flaky):
            outcome = fill_missing_bundle_slots(self.group)

        self.assertEqual(len(outcome["errors"]), 1)
        self.assertTrue(
            LessonVariant.objects.filter(learning_object=self.normal_tail, variant="ELABORATED").exists()
        )

    def test_a_role_supplied_by_a_pdf_is_never_generated(self):
        with patch("course.variant_generator._request_variants") as request:
            request.return_value = {"SIMPLIFIED": "Short.", "ELABORATED": "Longer text."}
            fill_missing_bundle_slots(self.group)

        self.assertFalse(
            LessonVariant.objects.filter(
                learning_object__in=[self.normal, self.normal_tail], variant="SIMPLIFIED",
            ).exists()
        )
```

Fill in `setUp` following `BundleRoleTests.setUp` from Task 4, adding `self.normal_tail` as a second object of the first material. Import `fill_missing_bundle_slots`, `VariantGenerationError` and `LessonVariant`.

- [ ] **Step 2: Run to verify it fails**

Run: `python manage.py test course.test_bundle_generation -v 2`
Expected: ImportError for `fill_missing_bundle_slots`.

- [ ] **Step 3: Implement**

In `backend/course/variant_generator.py`:

```python
def fill_missing_bundle_slots(group, target_slots=None, *, replace_stale=False):
    """Write the versions no PDF supplies, one object of the Normal bundle at a time.

    Generating a whole bundle in one call is where the local model starts
    returning invalid JSON, and a failure would cost the concept every version
    rather than one object's.
    """
    from .version_assignment import version_bundles

    bundles = version_bundles(group)
    normal = bundles.get("NORMAL") or []
    supplied = {role for role in bundles if role in ("SIMPLIFIED", "ELABORATED")}
    requested = [
        slot for slot in (target_slots or ("SIMPLIFIED", "ELABORATED"))
        if slot not in supplied
    ]
    generated, skipped, errors = [], [], []
    if not requested:
        return {"generated": generated, "skipped": sorted(supplied), "errors": errors}
    for learning_object in normal:
        outcome = fill_missing_slots(
            learning_object, requested, replace_stale=replace_stale,
        )
        generated.extend(outcome["generated"])
        skipped.extend(outcome["skipped"])
        errors.extend(outcome["errors"])
    return {"generated": generated, "skipped": skipped, "errors": errors}
```

In `settle_group` (`version_assignment.py`), replace `filled = fill_missing_slots(representative)` with `filled = fill_missing_bundle_slots(group)`, importing it from `.variant_generator` in the same lazy way the existing import is done.

- [ ] **Step 4: Run the tests**

Run: `python manage.py test course -v 2`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add course/variant_generator.py course/version_assignment.py course/test_bundle_generation.py
git commit -m "Generate missing versions per object of the Normal bundle

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Bundles and object controls in the API and UI

**Files:**
- Modify: `backend/lessons/views.py` (learning-resources payload; three new actions; merge/split actions deleted)
- Modify: `backend/lessons/serializers.py`
- Modify: `frontend/src/api.js`, `frontend/src/pages/TopicDetailPage.jsx`, `frontend/src/styles/pipeline.css`, `frontend/src/index.css`
- Test: `backend/lessons/test_bundle_controls.py` (new)

**Interfaces:**
- Consumes: `bundles_for_group`, `version_bundles`, `bundle_roles`, `set_bundle_role`, `place_unit`.
- Produces:
  - `POST outline-nodes/<node_id>/learning-objects/<object_id>/move-out` → learning-resources payload; the object becomes its own concept.
  - `POST outline-nodes/<node_id>/learning-objects/<object_id>/move-to` body `{"group_id": N}` → payload.
  - `POST outline-nodes/<node_id>/learning-objects/<object_id>/reorder` body `{"direction": "up" | "down"}` → payload; swaps `order` with the neighbouring object **of the same bundle**.
  - Payload: each group gains `"bundles": [{"material": id, "role": "NORMAL"|"SIMPLIFIED"|"ELABORATED"|"EXTRA"|null, "learning_objects": [...]}]`, materials in upload order.
  - JS: `moveObjectOut(courseId, nodeId, objectId)`, `moveObjectToConcept(courseId, nodeId, objectId, groupId)`, `reorderObject(courseId, nodeId, objectId, direction)`.

- [ ] **Step 1: Write the failing test**

Create `backend/lessons/test_bundle_controls.py`:

```python
"""The teacher's corrections to an automatic bundle.

Bundles are placed without asking, so every correction is a plain button: move
an object out, move it to another concept, or change its place in the bundle.
"""

from django.test import TestCase

from .models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
)
from .tests import authenticated_api_client


class BundleControlFixture(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="States")
        confirmed = {"learning_objects_confirmed": True}
        self.first = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="A", generated_json=dict(confirmed))
        self.second = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="B", generated_json=dict(confirmed))
        self.group = LearningObjectGroup.objects.create(outline_node=self.topic, label="Solid")
        self.solid = LearningObject.objects.create(
            material=self.first, group=self.group, title="Solid", order=0,
            content="A solid keeps its shape.")
        self.solids = LearningObject.objects.create(
            material=self.second, group=self.group, title="Solids", order=0,
            section_title="Solids", content="Packed tightly.")
        self.diagram = LearningObject.objects.create(
            material=self.second, group=self.group, title="Diagram description", order=1,
            section_title="Solids", content="Particles drawn in a grid.")

    def _url(self, suffix):
        return f"/api/courses/{self.course.id}/outline-nodes/{self.topic.id}/{suffix}"
```

Then the cases:

```python
class MoveOutTests(BundleControlFixture):
    def test_move_out_gives_the_object_its_own_concept(self):
        client = authenticated_api_client()

        response = client.post(self._url(f"learning-objects/{self.diagram.id}/move-out/"), format="json")

        self.assertEqual(response.status_code, 200, response.data)
        self.diagram.refresh_from_db()
        self.assertNotEqual(self.diagram.group_id, self.solid.group_id)
        self.assertEqual(self.diagram.group.learning_objects.count(), 1)


class MoveToTests(BundleControlFixture):
    def test_move_to_puts_the_object_in_the_named_concept(self):
        other = LearningObjectGroup.objects.create(outline_node=self.topic, label="Liquid")
        client = authenticated_api_client()

        response = client.post(
            self._url(f"learning-objects/{self.diagram.id}/move-to/"),
            {"group_id": other.id}, format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.diagram.refresh_from_db()
        self.assertEqual(self.diagram.group_id, other.id)

    def test_move_to_rejects_a_concept_from_another_topic(self):
        elsewhere = OutlineNode.objects.create(course=self.course, title="Other", order=1)
        foreign = LearningObjectGroup.objects.create(outline_node=elsewhere, label="Nope")
        client = authenticated_api_client()

        response = client.post(
            self._url(f"learning-objects/{self.diagram.id}/move-to/"),
            {"group_id": foreign.id}, format="json",
        )

        self.assertEqual(response.status_code, 400)


class ReorderTests(BundleControlFixture):
    def test_reorder_swaps_with_the_neighbour_in_the_same_bundle(self):
        client = authenticated_api_client()

        response = client.post(
            self._url(f"learning-objects/{self.diagram.id}/reorder/"),
            {"direction": "up"}, format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.solids.refresh_from_db()
        self.diagram.refresh_from_db()
        self.assertLess(self.diagram.order, self.solids.order)

    def test_reorder_at_the_edge_is_a_no_op(self):
        client = authenticated_api_client()

        response = client.post(
            self._url(f"learning-objects/{self.solids.id}/reorder/"),
            {"direction": "up"}, format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.solids.refresh_from_db()
        self.assertEqual(self.solids.order, 0)


class PayloadTests(BundleControlFixture):
    def test_the_payload_lists_bundles_per_pdf_in_order(self):
        client = authenticated_api_client()

        response = client.get(self._url("learning-resources/"))

        group = next(
            row for row in response.data["learning_object_groups"] if row["id"] == self.solid.group_id
        )
        bundles = {row["material"]: [item["id"] for item in row["learning_objects"]] for row in group["bundles"]}
        self.assertEqual(bundles[self.first.id], [self.solid.id])
        self.assertEqual(bundles[self.second.id], [self.solids.id, self.diagram.id])
```

- [ ] **Step 2: Run to verify it fails**

Run: `python manage.py test lessons.test_bundle_controls -v 2`
Expected: 404s for the three new routes and a `KeyError: 'bundles'`.

- [ ] **Step 3: Implement the endpoints**

Add three actions to the same viewset, next to `separate_learning_object`. Each resolves the node and object exactly as `split_learning_object_action` did (course-scoped, 404 when missing), then:

- **move-out**: create a new `LearningObjectGroup` for the node labelled with the object's title, set `learning_object.group` to it, clear `represented_by`, call `mark_grouping_current()`, delete the old group if it is now empty, and unpublish the topic if it was published.
- **move-to**: validate `group_id` is an integer naming a group of this node (400 otherwise), then move the object the same way.
- **reorder**: read `direction` (`"up"` or `"down"`, else 400), find the neighbouring object **in the same group and material** by `order`, and swap the two `order` values. No neighbour → return the payload unchanged.

All three end with `self._refresh_relationship_snapshots({learning_object.material}, recompute=False)` and `return Response(self._learning_resources_payload(node, request))`.

In `_learning_resources_payload`, add to each group's dict:

```python
                    "bundles": [
                        {
                            "material": material_id,
                            "role": roles.get(material_id, "NORMAL" if material_id == normal_id else None),
                            "learning_objects": LearningObjectSerializer(
                                objects, many=True, context={"request": request},
                            ).data,
                        }
                        for material_id, objects in sorted(
                            bundles_for_group(group).items(),
                            key=lambda pair: order_index.get(pair[0], len(order_index)),
                        )
                    ],
```

where `order_index = {mid: i for i, mid in enumerate(material_order(node))}`, `normal_id = normal_material_id(group)` and `roles = bundle_roles(group)`.

- [ ] **Step 4: Implement the UI**

In `frontend/src/api.js`:

```js
// Corrections to an automatic bundle: the object leaves, moves, or changes place.
export function moveObjectOut(courseId, nodeId, learningObjectId) {
  return request(
    `/courses/${courseId}/outline-nodes/${nodeId}/learning-objects/${learningObjectId}/move-out/`,
    { method: "POST" },
  );
}

export function moveObjectToConcept(courseId, nodeId, learningObjectId, groupId) {
  return request(
    `/courses/${courseId}/outline-nodes/${nodeId}/learning-objects/${learningObjectId}/move-to/`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ group_id: groupId }),
    },
  );
}

export function reorderObject(courseId, nodeId, learningObjectId, direction) {
  return request(
    `/courses/${courseId}/outline-nodes/${nodeId}/learning-objects/${learningObjectId}/reorder/`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ direction }),
    },
  );
}
```

In `TopicDetailPage.jsx`, in the Review Connections step: render each concept's `bundles` as one block per PDF (filename as a heading, objects listed in order), and give each object row four controls — **Move out**, a **Move to…** `<select>` of the topic's other concepts, **↑** and **↓**. Each calls its API function, then `setResources(data)`, with `busyAction` set to `` `move-${item.id}` `` while in flight, matching the existing handlers' shape. Remove the merge/split controls and the merge-confirmation text. Style `.concept-bundle` with the same spacing as `.connection-object-row` in **both** `styles/pipeline.css` and `index.css` (the two files are duplicates; `pipeline.css` is the one imported).

- [ ] **Step 5: Run the tests and build**

Run: `python manage.py test lessons -v 1`
Expected: all PASS.
Run (from `C:\MAVIA\frontend`): `npm run build`
Expected: builds with no errors. Do not commit `frontend/dist`.

- [ ] **Step 6: Commit**

```bash
git add lessons/views.py lessons/serializers.py lessons/test_bundle_controls.py ../frontend/src/api.js ../frontend/src/pages/TopicDetailPage.jsx ../frontend/src/styles/pipeline.css ../frontend/src/index.css
git commit -m "Show bundles per PDF and let the teacher correct them

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Learning path reads bundles

**Files:**
- Modify: `backend/learning_path/services/concept_units.py`
- Test: `backend/learning_path/test_criteria.py` (append to `DecidePairTests`), `backend/learning_path/test_concept_units.py`

**Interfaces:**
- Consumes: `lessons.services.concept_bundles.bundle_heading`, `ordered_members`.
- Produces: `Concept.member_text` in bundle order; `Concept.title` from the bundle's first heading.

- [ ] **Step 1: Write the failing test**

Append to `backend/learning_path/test_concept_units.py`:

```python
class BundleConceptTests(TestCase):
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
```

Add `_group`, `_object` and `_concept_for` helpers to the file's fixture if it has none: `_concept_for(group)` returns the matching `Concept` from `concepts_for_topic(self.topic)`.

- [ ] **Step 2: Run to verify it fails**

Run: `python manage.py test learning_path.test_concept_units -v 2`
Expected: FAIL — the concept is titled after the figure, and `member_text` is in scan order rather than bundle order.

- [ ] **Step 3: Implement**

In `concepts_for_topic` (`concept_units.py`):

- build members with `ordered_members(group)` instead of `list(group.learning_objects.all())`;
- compute `member_text` from those members in that order;
- set the Concept's `title` to `bundle_heading(members_of_the_representative_material) or representative.title`.

Leave `_merge_split_passages` and `_order_records` alone: part-suffix merging still applies to split passages.

- [ ] **Step 4: Run the learning-path suite**

Run: `python manage.py test learning_path -v 2`
Expected: all PASS, **including `GoldPathTests`** — the gold fixtures are unchanged and must still give 10/10 and 4/8 with the four known gaps.

- [ ] **Step 5: Commit**

```bash
git add learning_path/services/concept_units.py learning_path/test_concept_units.py
git commit -m "Read concepts as bundles in the learning path

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Delete the merge feature

**Files:**
- Delete: `backend/lessons/services/object_merge.py`, `backend/lessons/test_object_merge.py`
- Modify: `backend/lessons/models.py` (drop `merged_from`), `backend/lessons/serializers.py` (drop `merged_parts`), `backend/lessons/views.py` (drop the merge and split actions and their imports), `frontend/src/api.js` (drop `mergeLearningObjects`, `splitLearningObject`)
- Create: `backend/lessons/migrations/0019_drop_merged_from.py` (generated)

**Interfaces:**
- Produces: no merge/split endpoints, no `merged_from` column, no `merged_parts` serializer field.

- [ ] **Step 1: Delete the code**

```bash
git rm lessons/services/object_merge.py lessons/test_object_merge.py
```

Then remove: the `merged_from` field from `LearningObject`; `merged_parts` and its method from `LearningObjectSerializer` (and from `Meta.fields`); `merge_learning_objects_action`, `split_learning_object_action` and the `object_merge` import from `views.py`; `mergeLearningObjects` and `splitLearningObject` from `api.js` and their remaining call sites in `TopicDetailPage.jsx`.

- [ ] **Step 2: Generate and apply the migration**

Run: `python manage.py makemigrations lessons -n drop_merged_from`
Expected: one `RemoveField` operation.
Run: `python manage.py migrate lessons`
Expected: `Applying lessons.0019_drop_merged_from... OK`

- [ ] **Step 3: Run the suites**

Run: `python manage.py test lessons course learning_path -v 1`
Expected: all PASS with no references to the removed names.
Run (from `C:\MAVIA\frontend`): `npm run build`
Expected: builds with no errors.

- [ ] **Step 4: Commit**

```bash
git add -u lessons course ../frontend/src
git add lessons/migrations/0019_drop_merged_from.py
git commit -m "Remove the manual merge feature

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Consumers read a version as an ordered bundle

**Files:**
- Modify: `backend/course/services.py` (`_build_chunk`), `backend/course/models.py` (`normal_variant_for`), `backend/lessons/services/lesson_package.py`
- Test: `backend/course/test_representation.py` (append), `backend/lessons/tests.py` (the lesson-package tests)

**Interfaces:**
- Consumes: `version_bundles(group)`, `bundles_for_group`.
- Produces: a chunk's `variants[role]` gains `"segments": [{"text": ..., "audio_url": ...}]` in bundle order; `"text"` remains the joined text so existing readers keep working.

- [ ] **Step 1: Write the failing test**

Build the fixture exactly as `BundleGenerationTests.setUp` in Task 5 does (two objects in the Normal bundle from material A: "A solid keeps its shape." and "Particles sit in a grid."; material B supplying "Packed tight." and "Ice cubes." as the SIMPLIFIED bundle), then:

```python
class BundleChunkTests(TestCase):
    def test_a_versions_segments_follow_bundle_order(self):
        chunk = _build_chunk(self.normal)

        self.assertEqual(
            [segment["text"] for segment in chunk["variants"]["normal"]["segments"]],
            ["A solid keeps its shape.", "Particles sit in a grid."],
        )
        self.assertEqual(
            chunk["variants"]["normal"]["text"],
            "A solid keeps its shape.\nParticles sit in a grid.",
        )
        self.assertEqual(
            [segment["text"] for segment in chunk["variants"]["simplified"]["segments"]],
            ["Packed tight.", "Ice cubes."],
        )
```

- [ ] **Step 2: Run to verify it fails**

Run: `python manage.py test course.test_representation -v 2`
Expected: `KeyError: 'segments'`.

- [ ] **Step 3: Implement**

`_build_chunk` takes the concept's group, asks `version_bundles(group)` for each role's objects, and builds `{"text": bundle_text(objects), "segments": [{"text": item.content, "audio_url": <clip for item>} for item in objects], "origin": ...}`. Generated roles read their `LessonVariant` rows for the Normal bundle's objects, in the same order, and produce one segment per row. `normal_variant_for` keeps its signature but returns the joined Normal bundle text. `lesson_package.py` passes the segments through unchanged.

- [ ] **Step 4: Run the tests**

Run: `python manage.py test course lessons -v 1`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add course/services.py course/models.py lessons/services/lesson_package.py course/test_representation.py
git commit -m "Serve a version as its ordered bundle of segments

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9b: Questions come from the Normal bundle

**Files:**
- Modify: `backend/question_generation/services/pipeline.py`
- Test: `backend/question_generation/test_bundle_questions.py` (new)

**Read first:** `generate_questions_for_node`, `generate_questions_for_material` (or the equivalent loop over nodes near line 553) and `question_bank_fingerprint` — they read `node.content` and `node.title` of a single `LearningObject`.

**Interfaces:**
- Consumes: `course.version_assignment.version_bundles`, `lessons.services.concept_bundles.bundle_text`, `bundle_heading`.
- Produces: `concept_source_text(node) -> str` — the Normal bundle's text when the object belongs to a concept, else the object's own content. Question generation and the bank fingerprint both read it; the node a question links to is unchanged.

- [ ] **Step 1: Write the failing test**

```python
"""A concept's questions come from the whole Normal bundle.

The Normal track speaks every object of that bundle, so a bank generated from
the lead object alone would ask about a third of what the student hears.
"""

from unittest.mock import patch

from django.test import TestCase

from lessons.models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
)
from question_generation.services.pipeline import concept_source_text


class BundleQuestionSourceTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="States")
        self.material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="A",
            generated_json={"learning_objects_confirmed": True})
        self.group = LearningObjectGroup.objects.create(outline_node=self.topic, label="Solid")
        self.lead = LearningObject.objects.create(
            material=self.material, group=self.group, title="Solid", order=0,
            content="A solid keeps its shape.")
        self.tail = LearningObject.objects.create(
            material=self.material, group=self.group, title="Everyday examples", order=1,
            section_title="Solid", content="Ice cubes and a rock.")

    def test_the_source_text_is_the_whole_normal_bundle(self):
        self.assertEqual(
            concept_source_text(self.lead),
            "A solid keeps its shape.\nIce cubes and a rock.",
        )

    def test_an_ungrouped_object_uses_its_own_text(self):
        loose = LearningObject.objects.create(
            material=self.material, group=None, title="Loose", order=2, content="On its own.")

        self.assertEqual(concept_source_text(loose), "On its own.")

    def test_the_bank_fingerprint_follows_the_bundle(self):
        from question_generation.services.pipeline import question_bank_fingerprint, QUESTION_DISTRIBUTION

        before = question_bank_fingerprint(concept_source_text(self.lead), QUESTION_DISTRIBUTION)
        self.tail.content = "Ice cubes, a rock and a coin."
        self.tail.save(update_fields=["content"])

        after = question_bank_fingerprint(concept_source_text(self.lead), QUESTION_DISTRIBUTION)
        self.assertNotEqual(before, after)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python manage.py test question_generation.test_bundle_questions -v 2`
Expected: ImportError for `concept_source_text`.

- [ ] **Step 3: Implement**

In `backend/question_generation/services/pipeline.py`:

```python
def concept_source_text(node):
    """The text a concept's questions are written from.

    A concept may be taught as several objects of one PDF, and the Normal
    track speaks all of them. Generating from the lead object alone would ask
    about a fraction of what the student hears.
    """
    from course.version_assignment import version_bundles
    from lessons.services.concept_bundles import bundle_text

    if node.group_id is None:
        return node.content or ""
    normal = version_bundles(node.group).get("NORMAL") or []
    if not any(item.id == node.id for item in normal):
        return node.content or ""
    return bundle_text(normal) or (node.content or "")
```

Then use it in place of `node.content` where the generator builds its prompt (the `content=node.content` call around line 252) and where the bank fingerprint is computed (around line 502). Leave `node=node` on the saved rows untouched: a question still belongs to the object that leads its concept.

- [ ] **Step 4: Run the suite**

Run: `python manage.py test question_generation -v 1`
Expected: all PASS. A test that pinned the prompt to a single object's text encodes the replaced behaviour — update it with `# Changed 2026-09-20: questions read the Normal bundle.` and list it in your report.

- [ ] **Step 5: Commit**

```bash
git add question_generation/services/pipeline.py question_generation/test_bundle_questions.py
git commit -m "Write questions from the concept's whole Normal bundle

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: Verify on a clean upload

This task changes the user's database and needs them in the UI. **Ask before Step 2.**

**Files:**
- Modify: `docs/learning_path_revision_2026-09-17.md` (a "Bundles re-run" section)

- [ ] **Step 1: Full suite**

Run: `python manage.py test -v 1`
Expected: OK, with `GoldPathTests` passing.

- [ ] **Step 2: Clean upload (with the user)**

The user deletes the course and uploads both PDFs into each topic again, confirming the objects per file. No merging exists any more; bundles form during grouping.

- [ ] **Step 3: Check what formed automatically**

Run:
```
python manage.py shell -c "from lessons.models import OutlineNode; [print(n.id, n.title, [[o.title for o in g.learning_objects.order_by('material_id','order')] for g in n.learning_object_groups.all() if g.learning_objects.exists()]) for n in OutlineNode.objects.filter(materials__isnull=False).distinct()]"
```
Record which concepts formed without a card, and which cards were raised.

- [ ] **Step 4: The user reviews the cards and publishes both topics.**

- [ ] **Step 5: Compare with the gold standard**

Run `python manage.py evaluate_gold_paths` for the fixtures, and compare the live topics' accepted edges and order against the gold lists in the spec's §3 (topic 62 shape: 10/10; topic 79: 4/8 with the four known gaps; 0 forbidden; orders matching).

- [ ] **Step 6: Record the result**

Append a "Bundles re-run (2026-09-20)" section to `docs/learning_path_revision_2026-09-17.md`: how many concepts formed automatically, how many cards the teacher saw, the required/forbidden/order numbers per topic, and any difference from the merge-based run — reported as a finding, not tuned away.

- [ ] **Step 7: Commit**

```bash
git add ../docs/learning_path_revision_2026-09-17.md ../frontend/dist
git commit -m "Record the bundles re-run on a clean upload

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```
