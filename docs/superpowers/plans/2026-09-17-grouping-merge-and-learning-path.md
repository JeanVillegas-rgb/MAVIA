# Grouping Merges and Revised Learning-Path Criteria Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a concept hold content that one PDF splits into several objects (merge/split, heading-matched suggestions), and revise the learning-path criteria so the two real lessons produce the teacher's gold-standard edges and order.

**Architecture:** A new `lessons/services/object_merge.py` merges several same-PDF learning objects into one row, keeping a JSON snapshot for undo; manual and heading-matched suggestions both call it. The learning path keeps its three-criterion vote and Kahn ordering, but semantic reference becomes RefD-style key-term reference (`criteria.reference_matrix`), Examples concepts are excluded and placed last, and a gold-standard test built from real lesson text is the acceptance gate.

**Tech Stack:** Django 5.2, Django REST Framework, SQLite, sentence-transformers (all-MiniLM-L6-v2, cross-encoder/stsb-roberta-base), React 18 + Vite.

**Spec:** `docs/superpowers/specs/2026-09-17-grouping-merge-and-learning-path-design.md`

## Global Constraints

- Keep a scored prerequisite graph ordered by Kahn's topological sort ("Selection of Learning Object – Learning Path (adaptive graph traversal and scoring algorithm)").
- Keep the three criteria (temporal order, semantic reference, foundationality) and the vote: 3/3 accepted, 2/3 pending, otherwise none.
- No generative model in the criteria; the same concepts always give the same graph.
- Teacher decisions (approved/rejected edges, accepted/rejected suggestions) are never overwritten.
- Merges are never applied automatically; unit suggestions are always `pending`.
- Do not modify extraction code (`lessons/services/content_generator.py`, `image_describer.py`) — owned by a groupmate.
- Stop condition: if a required gold edge cannot be reached by text evidence without a lesson-specific rule, stop and report to the user.
- All commands run from `C:\MAVIA\backend` unless stated. Tests: `python manage.py test <label> -v 2`.
- Every commit message ends with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- The working tree has unrelated uncommitted changes in `backend/course/tests.py` and `backend/course/variant_generator.py` owned by the user. Never stage them: always `git add` explicit paths.

## File Structure

| File | Responsibility |
|---|---|
| `backend/lessons/models.py` | `LearningObject.merged_from`; `LearningObjectMatchSuggestion.source_extra_ids` / `candidate_extra_ids` |
| `backend/lessons/migrations/0018_merge_fields.py` | Migration for the three fields |
| `backend/lessons/services/object_merge.py` (new) | `merge_text`, `merge_learning_objects`, `split_learning_object`, `choose_kept_row`, `MergeError` |
| `backend/lessons/services/unit_matching.py` (new) | `heading_key`, `find_units`, `heading_unit_candidates`, `refresh_heading_unit_suggestions` |
| `backend/lessons/services/learning_resource_linker.py` | Call `refresh_heading_unit_suggestions` after one-to-one refresh |
| `backend/lessons/views.py` | Merge and split endpoints; accept path merges unit extras |
| `backend/lessons/serializers.py` | `merged_parts` on objects; `source_members` / `candidate_members` on suggestions |
| `backend/lessons/signals.py` (new), `backend/lessons/apps.py` | Delete stored files when rows are deleted |
| `backend/lessons/management/commands/clean_orphan_media.py` (new) | List/delete unreferenced media files |
| `backend/config/settings.py` | Temporary `MEDIA_ROOT` under tests |
| `backend/learning_path/services/concepts.py` | `strip_numbering`, `is_structural`, heading-name fallback |
| `backend/learning_path/services/concept_units.py` | `Concept.member_text` |
| `backend/learning_path/services/criteria.py` | RefD key-term reference, margin, "than" veto, no cross-section cap |
| `backend/learning_path/services/publishing.py` | Structural concepts ordered last |
| `backend/learning_path/services/gold.py` (new) | `load_gold`, `gold_report` |
| `backend/learning_path/fixtures/gold_map_62.json`, `gold_map_79.json` (new) | Teacher's concept map and gold edges |
| `backend/learning_path/fixtures/gold_topic_62.json`, `gold_topic_79.json` (generated) | Real lesson text per gold concept |
| `backend/learning_path/management/commands/export_gold_concepts.py` (new) | Build gold fixtures from live objects |
| `backend/learning_path/management/commands/evaluate_gold_paths.py` (new) | Print gold report; `--grid` calibration |
| `backend/learning_path/test_gold_paths.py` (new) | Acceptance test against gold |
| `frontend/src/api.js`, `frontend/src/pages/TopicDetailPage.jsx` | Merge/split buttons; unit suggestion cards |
| `docs/learning_path_revision_2026-09-17.md` (new) | Before/after report for the manuscript |

Task order: 1 merge service → 2 gold fixtures and baseline → 3 manual merge/split API and UI → 4 heading-matched suggestions → 5 concept surface → 6 RefD criteria → 7 calibration and report → 8 media cleanup → 9 live re-run.

**Task 2 must run before any merge is performed on the live database** (the exporter reads learning objects by id; merges delete ids).

---

### Task 1: Merge and split service

**Files:**
- Modify: `backend/lessons/models.py` (class `LearningObject` near line 173; class `LearningObjectMatchSuggestion` near line 245)
- Create: `backend/lessons/migrations/0018_merge_fields.py` (generated)
- Create: `backend/lessons/services/object_merge.py`
- Test: `backend/lessons/test_object_merge.py`

**Interfaces:**
- Produces:
  - `merge_text(members: list[LearningObject]) -> str` — `"<title>: <content>"` per member, newline-joined, in the given order.
  - `choose_kept_row(members) -> LearningObject` — raises `MergeError`.
  - `merge_learning_objects(members, *, title: str | None = None) -> tuple[LearningObject, bool]` — returns `(kept_row, unpublished)`.
  - `split_learning_object(learning_object) -> tuple[list[LearningObject], bool]` — returns `(restored_rows_in_document_order, unpublished)`.
  - `class MergeError(ValueError)`.
  - Model fields: `LearningObject.merged_from` (JSON list), `LearningObjectMatchSuggestion.source_extra_ids`, `candidate_extra_ids` (JSON lists).

- [ ] **Step 1: Add the model fields**

In `LearningObject` (after `grouping_content_hash`):

```python
    # Set when several objects from this PDF were merged into this row: one
    # snapshot per original member, in document order, so "Split back" can
    # rebuild them with their original metadata ids and question links.
    merged_from = models.JSONField(default=list, blank=True)
```

In `LearningObjectMatchSuggestion` (after `status`):

```python
    # A heading-matched unit suggestion names more than one object per side.
    # The FKs hold each side's kept row; these hold the other members.
    source_extra_ids = models.JSONField(default=list, blank=True)
    candidate_extra_ids = models.JSONField(default=list, blank=True)
```

In `LearningObjectMatchSuggestion.clean`, skip the same-kind check when either extras list is non-empty. Find the line setting `errors["candidate_learning_object"] = "Suggested objects must have the same content type."` and guard its condition with `and not (self.source_extra_ids or self.candidate_extra_ids)`.

- [ ] **Step 2: Generate and apply the migration**

Run: `python manage.py makemigrations lessons -n merge_fields`
Expected: creates `lessons/migrations/0018_merge_fields.py` with three `AddField` operations.
Run: `python manage.py migrate lessons`
Expected: `Applying lessons.0018_merge_fields... OK`

- [ ] **Step 3: Write the failing tests**

Create `backend/lessons/test_object_merge.py`:

```python
"""Merging several objects of one PDF into one, and splitting them back.

A concept may be taught as four short items in one PDF and one section in
another. Grouping admits one object per PDF, so the four become one row. The
merge must be fully reversible: split restores metadata ids, order and question
links exactly.
"""

from django.test import TestCase

from course.models import LessonVariant
from question_generation.models import GeneratedQuestion

from .models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
    Question,
    QuestionLearningObjectLink,
)
from .services.object_merge import (
    MergeError,
    merge_learning_objects,
    merge_text,
    split_learning_object,
)


class MergeFixture(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="States")
        self.material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="First",
            generated_json={
                "learning_objects_confirmed": True,
                "lesson_playlist": [],
            },
        )
        self.other = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="Second",
            generated_json={"learning_objects_confirmed": True},
        )
        self.intro = self._object(self.material, "Matter", "Matter has mass.", 0, "Matter")
        self.shape = self._object(self.material, "Shape", "Solids keep their shape.", 1, "Comparing")
        self.volume = self._object(self.material, "Volume", "Gases fill space.", 2, "Comparing")
        self.flow = self._object(self.material, "Flow", "Liquids flow.", 3, "Comparing")
        self.after = self._object(self.material, "Examples", "Ice.", 4, "")
        self.comparing = self._object(self.other, "Comparing", "The table compares.", 0, "Comparing")
        self.material.generated_json["lesson_playlist"] = [
            {"learning_object_id": item.id, "narration": item.content, "audio_url": ""}
            for item in (self.shape, self.volume, self.after)
        ]
        self.material.save(update_fields=["generated_json"])

    def _object(self, material, title, content, order, section):
        group = LearningObjectGroup.objects.create(outline_node=self.topic, label=title)
        return LearningObject.objects.create(
            material=material, group=group, title=title, content=content,
            order=order, section_title=section,
        )

    def _question(self, learning_object, *, primary=True):
        question = Question.objects.create(material=self.material, prompt=f"About {learning_object.title}?")
        QuestionLearningObjectLink.objects.create(
            question=question, learning_object=learning_object, is_primary=primary,
        )
        return question

    def _generated(self, learning_object):
        return GeneratedQuestion.objects.create(
            node=learning_object, question_text="Q?", question_format="TF", correct_answer="True",
        )


class MergeTextTests(MergeFixture):
    def test_each_member_keeps_its_title_as_a_lead_in(self):
        self.assertEqual(
            merge_text([self.shape, self.volume]),
            "Shape: Solids keep their shape.\nVolume: Gases fill space.",
        )


class MergeTests(MergeFixture):
    def test_members_become_one_row_titled_by_their_heading(self):
        kept, _ = merge_learning_objects([self.volume, self.shape, self.flow])

        self.assertEqual(kept.id, self.shape.id)
        self.assertEqual(kept.title, "Comparing")
        self.assertEqual(
            kept.content,
            "Shape: Solids keep their shape.\nVolume: Gases fill space.\nFlow: Liquids flow.",
        )
        self.assertEqual(kept.kind, LearningObject.Kind.TEXT)
        self.assertFalse(LearningObject.objects.filter(pk__in=[self.volume.id, self.flow.id]).exists())
        self.assertEqual([entry["title"] for entry in kept.merged_from], ["Shape", "Volume", "Flow"])

    def test_orders_are_renumbered_without_gaps(self):
        merge_learning_objects([self.shape, self.volume, self.flow])

        rows = list(self.material.learning_objects.order_by("order").values_list("title", "order"))
        self.assertEqual(rows, [("Matter", 0), ("Comparing", 1), ("Examples", 2)])

    def test_question_links_and_generated_questions_move_to_the_kept_row(self):
        question = self._question(self.volume)
        generated = self._generated(self.flow)

        kept, _ = merge_learning_objects([self.shape, self.volume, self.flow])

        self.assertEqual(QuestionLearningObjectLink.objects.get(question=question).learning_object_id, kept.id)
        generated.refresh_from_db()
        self.assertEqual(generated.node_id, kept.id)

    def test_a_duplicate_link_keeps_the_primary_flag(self):
        question = Question.objects.create(material=self.material, prompt="Both?")
        QuestionLearningObjectLink.objects.create(question=question, learning_object=self.shape, is_primary=False)
        QuestionLearningObjectLink.objects.create(question=question, learning_object=self.volume, is_primary=True)

        kept, _ = merge_learning_objects([self.shape, self.volume])

        link = QuestionLearningObjectLink.objects.get(question=question)
        self.assertEqual(link.learning_object_id, kept.id)
        self.assertTrue(link.is_primary)

    def test_versions_and_audio_of_members_are_dropped(self):
        LessonVariant.objects.create(learning_object=self.shape, variant="SIMPLIFIED", narration="Short.")

        merge_learning_objects([self.shape, self.volume])

        self.assertFalse(LessonVariant.objects.filter(learning_object=self.shape).exists())
        self.material.refresh_from_db()
        playlist_ids = [entry["learning_object_id"] for entry in self.material.generated_json["lesson_playlist"]]
        self.assertEqual(playlist_ids, [self.after.id])

    def test_the_member_already_connected_elsewhere_is_kept(self):
        self.comparing.group = self.flow.group
        self.comparing.save(update_fields=["group"])

        kept, _ = merge_learning_objects([self.shape, self.volume, self.flow])

        self.assertEqual(kept.id, self.flow.id)
        self.assertEqual(kept.group_id, self.comparing.group_id)

    def test_two_members_connected_elsewhere_are_refused(self):
        self.comparing.group = self.flow.group
        self.comparing.save(update_fields=["group"])
        extra = self._object(self.other, "Shape too", "Shape.", 1, "")
        extra.group = self.shape.group
        extra.save(update_fields=["group"])

        with self.assertRaises(MergeError):
            merge_learning_objects([self.shape, self.flow])

    def test_objects_from_different_pdfs_are_refused(self):
        with self.assertRaises(MergeError):
            merge_learning_objects([self.shape, self.comparing])

    def test_a_single_object_is_refused(self):
        with self.assertRaises(MergeError):
            merge_learning_objects([self.shape])

    def test_a_published_topic_is_unpublished(self):
        OutlineNode.objects.filter(pk=self.topic.pk).update(published=True)

        _, unpublished = merge_learning_objects([self.shape, self.volume])

        self.topic.refresh_from_db()
        self.assertTrue(unpublished)
        self.assertFalse(self.topic.published)


class SplitTests(MergeFixture):
    def test_split_restores_rows_ids_titles_and_order(self):
        metadata = {item.title: item.metadata_id for item in (self.shape, self.volume, self.flow)}
        kept, _ = merge_learning_objects([self.shape, self.volume, self.flow])

        restored, _ = split_learning_object(kept)

        self.assertEqual([row.title for row in restored], ["Shape", "Volume", "Flow"])
        self.assertEqual({row.title: row.metadata_id for row in restored}, metadata)
        rows = list(self.material.learning_objects.order_by("order").values_list("title", "order"))
        self.assertEqual(
            rows,
            [("Matter", 0), ("Shape", 1), ("Volume", 2), ("Flow", 3), ("Examples", 4)],
        )
        self.assertTrue(all(row.merged_from == [] for row in restored))

    def test_split_moves_questions_back_including_dropped_duplicates(self):
        moved = self._question(self.volume)
        both = Question.objects.create(material=self.material, prompt="Both?")
        QuestionLearningObjectLink.objects.create(question=both, learning_object=self.shape, is_primary=True)
        QuestionLearningObjectLink.objects.create(question=both, learning_object=self.volume, is_primary=False)
        generated = self._generated(self.volume)
        kept, _ = merge_learning_objects([self.shape, self.volume])

        restored, _ = split_learning_object(kept)

        volume = next(row for row in restored if row.title == "Volume")
        shape = next(row for row in restored if row.title == "Shape")
        self.assertEqual(QuestionLearningObjectLink.objects.get(question=moved).learning_object_id, volume.id)
        self.assertEqual(
            set(QuestionLearningObjectLink.objects.filter(question=both).values_list("learning_object_id", "is_primary")),
            {(shape.id, True), (volume.id, False)},
        )
        generated.refresh_from_db()
        self.assertEqual(generated.node_id, volume.id)

    def test_restored_rows_start_ungrouped(self):
        self.comparing.group = self.shape.group
        self.comparing.save(update_fields=["group"])
        kept, _ = merge_learning_objects([self.shape, self.volume])

        restored, _ = split_learning_object(kept)

        self.assertTrue(all(row.group_id is None for row in restored))
        self.comparing.refresh_from_db()
        self.assertIsNotNone(self.comparing.group_id)

    def test_an_unmerged_object_cannot_be_split(self):
        with self.assertRaises(MergeError):
            split_learning_object(self.shape)
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `python manage.py test lessons.test_object_merge -v 2`
Expected: ERROR, `ModuleNotFoundError: No module named 'lessons.services.object_merge'`

- [ ] **Step 5: Implement the service**

Create `backend/lessons/services/object_merge.py`:

```python
"""Merge several learning objects of one PDF into one row, and split them back.

One PDF may teach a concept as four short items ("Shape", "Volume", ...) while
another teaches it as one section. Grouping admits one object per PDF, and
everything downstream -- version assignment, audio, the mobile package -- keys
on a concept's single representative row. Merging the items into one row keeps
all of that working unchanged.

The merge is reversible. The kept row stores a snapshot of every original
member, so "Split back" restores them with their original metadata ids, order
and question links. Hidden rows were rejected: every module that queries
learning objects would have to learn to skip them.
"""

import uuid

from django.db import transaction
from django.db.models import F

from course.models import LessonVariant
from course.version_assignment import release_from_group
from question_generation.models import GeneratedQuestion

from ..models import (
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
    Question,
    QuestionLearningObjectLink,
)

SNAPSHOT_FIELDS = (
    "title", "content", "kind", "order", "section_title", "source_page",
    "source_block_id", "source_excerpt", "image_url", "image_prompt",
)


class MergeError(ValueError):
    """A merge or split the teacher asked for that cannot be carried out."""


def merge_text(members):
    """The merged content: each member's title as a lead-in, in the given order."""
    return "\n".join(
        f"{item.title}: {item.content}".strip() for item in members
    )


def _connected_elsewhere(item):
    return bool(
        item.group_id
        and LearningObject.objects.filter(group_id=item.group_id).exclude(pk=item.pk).exists()
    )


def choose_kept_row(members):
    """The row that survives: the one already connected to other objects, if any.

    Keeping that row keeps its group, so the other PDFs' versions of the concept
    stay attached. Two such rows would mean two different concepts.
    """
    connected = [item for item in members if _connected_elsewhere(item)]
    if len(connected) > 1:
        raise MergeError(
            "More than one of these objects is already connected to other objects. "
            "Separate them first."
        )
    if connected:
        return connected[0]
    return min(members, key=lambda item: (item.order, item.id))


def _renumber(material):
    for index, item in enumerate(material.learning_objects.order_by("order", "id")):
        if item.order != index:
            LearningObject.objects.filter(pk=item.pk).update(order=index)


def _drop_playlist_entries(material, object_ids):
    data = dict(material.generated_json or {})
    playlist = data.get("lesson_playlist") or []
    kept_entries = [entry for entry in playlist if entry.get("learning_object_id") not in object_ids]
    if len(kept_entries) != len(playlist):
        data["lesson_playlist"] = kept_entries
        material.generated_json = data
        material.save(update_fields=["generated_json"])


def _remove_empty_groups(node_id):
    LearningObjectGroup.objects.filter(
        outline_node_id=node_id, learning_objects__isnull=True,
    ).delete()


def _unpublish(node_id):
    # Read the flag from the database: callers hold cached topic instances.
    if node_id is None:
        return False
    return bool(
        OutlineNode.objects.filter(pk=node_id, published=True)
        .update(published=False, published_at=None)
    )


def _snapshot(item):
    return {
        "id": item.id,
        "metadata_id": str(item.metadata_id),
        **{name: getattr(item, name) for name in SNAPSHOT_FIELDS},
        "question_links": [
            {
                "id": link.id,
                "question_id": link.question_id,
                "relevance_score": link.relevance_score,
                "method": link.method,
                "is_primary": link.is_primary,
                "review_status": link.review_status,
            }
            for link in QuestionLearningObjectLink.objects.filter(learning_object=item)
        ],
        "generated_question_ids": list(
            GeneratedQuestion.objects.filter(node=item).values_list("id", flat=True)
        ),
    }


def _move_links(source, kept):
    for link in QuestionLearningObjectLink.objects.filter(learning_object=source):
        duplicate = QuestionLearningObjectLink.objects.filter(
            question_id=link.question_id, learning_object=kept,
        ).first()
        if duplicate is None:
            link.learning_object = kept
            link.save(update_fields=["learning_object"])
            continue
        promote = link.is_primary and not duplicate.is_primary
        link.delete()
        if promote:
            duplicate.is_primary = True
            duplicate.save(update_fields=["is_primary"])
    GeneratedQuestion.objects.filter(node=source).update(node=kept)


@transaction.atomic
def merge_learning_objects(members, *, title=None):
    """Merge ``members`` (one PDF) into one row. Returns ``(kept, unpublished)``."""
    members = sorted(members, key=lambda item: (item.order, item.id))
    if len(members) < 2:
        raise MergeError("Select at least two learning objects to merge.")
    if len({item.material_id for item in members}) != 1:
        raise MergeError("Only objects from the same PDF can be merged into one.")
    if any(item.merged_from for item in members):
        raise MergeError("Split an already merged object back before merging it again.")

    kept = choose_kept_row(members)
    material = kept.material
    first = members[0]
    snapshot = [_snapshot(item) for item in members]
    others = [item for item in members if item.pk != kept.pk]
    member_ids = [item.id for item in members]

    for item in others:
        _move_links(item, kept)
    LessonVariant.objects.filter(learning_object_id__in=member_ids).delete()
    LessonVariant.objects.filter(source_learning_object_id__in=member_ids).delete()

    kept.title = (title or first.section_title or first.title)[:255]
    kept.content = merge_text(members)
    kept.kind = LearningObject.Kind.TEXT
    kept.order = first.order
    kept.section_title = first.section_title
    kept.image_url = ""
    kept.merged_from = snapshot
    kept.mark_grouping_current()
    kept.save()
    if kept.group_id:
        # The stored representative may have been one of the deleted rows.
        LearningObjectGroup.objects.filter(pk=kept.group_id).update(version_selection={})

    LearningObject.objects.filter(pk__in=[item.pk for item in others]).delete()
    _renumber(material)
    _drop_playlist_entries(material, member_ids)
    _remove_empty_groups(material.outline_node_id)
    kept.refresh_from_db()
    return kept, _unpublish(material.outline_node_id)


@transaction.atomic
def split_learning_object(learning_object):
    """Rebuild a merged row's originals. Returns ``(rows, unpublished)``."""
    kept = learning_object
    snapshot = list(kept.merged_from or [])
    if not snapshot:
        raise MergeError("This learning object was not created by a merge.")
    material = kept.material
    base = kept.order

    LearningObject.objects.filter(material=material, order__gt=base).update(
        order=F("order") + len(snapshot) - 1,
    )
    if kept.group_id:
        companions = list(LearningObject.objects.filter(group_id=kept.group_id).exclude(pk=kept.pk))
        release_from_group(kept, companions)
        LearningObjectGroup.objects.filter(pk=kept.group_id).update(version_selection={})
    LessonVariant.objects.filter(learning_object=kept).delete()
    LessonVariant.objects.filter(source_learning_object=kept).delete()
    _drop_playlist_entries(material, [kept.id])

    restored = []
    for index, entry in enumerate(snapshot):
        row = kept if entry["id"] == kept.id else LearningObject(
            material=material, metadata_id=uuid.UUID(entry["metadata_id"]),
        )
        for name in SNAPSHOT_FIELDS:
            setattr(row, name, entry[name])
        row.order = base + index
        row.group = None
        row.represented_by = None
        row.merged_from = []
        row.grouping_content_hash = ""
        row.save()
        restored.append((entry, row))

    for entry, row in restored:
        if row.pk != kept.pk:
            GeneratedQuestion.objects.filter(
                pk__in=entry["generated_question_ids"], node=kept,
            ).update(node=row)
        for link in entry["question_links"]:
            if not Question.objects.filter(pk=link["question_id"]).exists():
                continue
            existing = QuestionLearningObjectLink.objects.filter(pk=link["id"]).first()
            if existing is not None:
                if existing.learning_object_id != row.id:
                    existing.learning_object = row
                    existing.is_primary = False
                    existing.save(update_fields=["learning_object", "is_primary"])
            elif not QuestionLearningObjectLink.objects.filter(
                question_id=link["question_id"], learning_object=row,
            ).exists():
                QuestionLearningObjectLink.objects.create(
                    question_id=link["question_id"], learning_object=row,
                    relevance_score=link["relevance_score"], method=link["method"],
                    is_primary=False, review_status=link["review_status"],
                )

    # Primary flags last: at most one primary per question at any moment.
    for entry, row in restored:
        for link in entry["question_links"]:
            QuestionLearningObjectLink.objects.filter(
                question_id=link["question_id"], learning_object=row,
            ).update(is_primary=False)
    for entry, row in restored:
        for link in entry["question_links"]:
            if link["is_primary"]:
                QuestionLearningObjectLink.objects.filter(
                    question_id=link["question_id"],
                ).update(is_primary=False)
                QuestionLearningObjectLink.objects.filter(
                    question_id=link["question_id"], learning_object=row,
                ).update(is_primary=True)

    _remove_empty_groups(material.outline_node_id)
    return [row for _, row in restored], _unpublish(material.outline_node_id)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python manage.py test lessons.test_object_merge -v 2`
Expected: all 15 tests PASS.

If `test_restored_rows_start_ungrouped` fails because `release_from_group` expects a group with a stored selection, read `course/version_assignment.py:569-660` and pass the companions exactly as `accept_match_suggestion` does in `lessons/views.py:733-737`; do not change `release_from_group`.

- [ ] **Step 7: Run the lessons suite for regressions**

Run: `python manage.py test lessons -v 1`
Expected: same pass/fail counts as before this task plus 15 new passes. If a pre-existing test fails, run it on `git stash` of only your files to confirm it was already failing; report it, do not fix it here.

- [ ] **Step 8: Commit**

```bash
git add lessons/models.py lessons/migrations/0018_merge_fields.py lessons/services/object_merge.py lessons/test_object_merge.py
git commit -m "Add reversible merge of same-PDF learning objects

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Gold-standard fixtures and baseline report

**Files:**
- Create: `backend/learning_path/fixtures/gold_map_62.json`, `backend/learning_path/fixtures/gold_map_79.json`
- Create: `backend/learning_path/management/commands/export_gold_concepts.py`
- Create: `backend/learning_path/services/gold.py`
- Create: `backend/learning_path/management/commands/evaluate_gold_paths.py`
- Create: `backend/learning_path/test_gold_paths.py`
- Create (generated): `backend/learning_path/fixtures/gold_topic_62.json`, `gold_topic_79.json`
- Create: `docs/learning_path_revision_2026-09-17.md`

**Interfaces:**
- Consumes: `lessons.services.object_merge.merge_text(members)` (Task 1); `criteria.decide_pairs(concepts, runtime_instance)`, `criteria.ACCEPTED`, `criteria.PENDING`; `publishing.order_with_links(concepts, links) -> (ordered, depth, ignored)`.
- Produces:
  - `gold.load_gold(topic_id: int) -> tuple[dict, list[SimpleNamespace]]`; each concept has `id` (1-based), `key`, `title`, `content`, `member_text`, `section_title`, `kind`, `order`, `members` (tuple of namespaces with `title`, `section_title`, `content`, `material_id`, `order`).
  - `gold.gold_report(data, concepts, decisions) -> dict` with keys `topic`, `accepted`, `pending`, `missing_required`, `forbidden_accepted`, `extra_accepted`, `order`, `order_matches`, `ignored_links` (edge lists are `[prerequisite_key, dependent_key]`).
  - Management command `evaluate_gold_paths [--grid]`.

- [ ] **Step 1: Write the concept maps**

Create `backend/learning_path/fixtures/gold_map_62.json`:

```json
{
  "topic_id": 62,
  "concepts": [
    {"key": "matter", "title": "Matter", "section_title": "Matter", "member_ids": [159, 160, 170, 171]},
    {"key": "solid", "title": "Solid", "section_title": "", "member_ids": [161, 172, 173, 174]},
    {"key": "liquid", "title": "Liquid", "section_title": "", "member_ids": [162, 175, 176, 177, 178]},
    {"key": "gas", "title": "Gas", "section_title": "", "member_ids": [163, 179, 180, 181]},
    {"key": "comparing", "title": "Comparing the Three States", "section_title": "Comparing the Three States", "member_ids": [164, 165, 166, 167, 182, 183]},
    {"key": "changing", "title": "Changing From One State to Another", "section_title": "", "member_ids": [184, 185, 186]},
    {"key": "examples", "title": "Everyday Examples", "section_title": "", "member_ids": [168, 187, 188]}
  ],
  "required": [
    ["matter", "solid"], ["matter", "liquid"], ["matter", "gas"],
    ["solid", "comparing"], ["liquid", "comparing"], ["gas", "comparing"],
    ["solid", "changing"], ["liquid", "changing"], ["gas", "changing"],
    ["comparing", "changing"]
  ],
  "parallel": [["solid", "liquid", "gas"]],
  "structural": ["examples"],
  "expected_order": ["matter", "solid", "liquid", "gas", "comparing", "changing", "examples"]
}
```

Create `backend/learning_path/fixtures/gold_map_79.json`:

```json
{
  "topic_id": 79,
  "concepts": [
    {"key": "reproduction", "title": "Reproduction in Flowering Plants", "section_title": "", "member_ids": [138, 139, 148, 149, 150]},
    {"key": "stamen", "title": "Stamen (Male Part)", "section_title": "Reproduction in Flowering Plants", "member_ids": [140, 151]},
    {"key": "pistil", "title": "Pistil (Female Part)", "section_title": "Reproduction in Flowering Plants", "member_ids": [141, 152]},
    {"key": "petals", "title": "Petals and Sepals (Supporting Parts)", "section_title": "Reproduction in Flowering Plants", "member_ids": [142, 153]},
    {"key": "pollination", "title": "Pollination", "section_title": "How Flowering Plants Reproduce", "member_ids": [143, 154]},
    {"key": "fertilization", "title": "Fertilization", "section_title": "How Flowering Plants Reproduce", "member_ids": [144, 155]},
    {"key": "seed", "title": "Seed formation", "section_title": "How Flowering Plants Reproduce", "member_ids": [145, 156]},
    {"key": "fruit", "title": "Fruit formation", "section_title": "How Flowering Plants Reproduce", "member_ids": [146, 157]},
    {"key": "examples", "title": "Everyday Examples", "section_title": "", "member_ids": [147, 158]}
  ],
  "required": [
    ["reproduction", "stamen"], ["reproduction", "pistil"], ["reproduction", "petals"],
    ["stamen", "pollination"], ["pistil", "pollination"],
    ["pollination", "fertilization"], ["fertilization", "seed"], ["seed", "fruit"]
  ],
  "parallel": [],
  "structural": ["examples"],
  "expected_order": ["reproduction", "stamen", "pistil", "petals", "pollination", "fertilization", "seed", "fruit", "examples"]
}
```

- [ ] **Step 2: Write the exporter**

Create `backend/learning_path/management/commands/export_gold_concepts.py`:

```python
"""Build a gold learning-path fixture from a teacher's concept map.

The map lists which live learning objects make up each gold concept. Several
objects from one PDF are merged exactly as the merge service would merge them,
so the fixture holds the text the criteria will see after merging -- without
changing the live database. Run it before merging anything live: merges delete
the ids the map refers to.
"""

import json
from collections import defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from lessons.models import LearningObject
from lessons.services.object_merge import merge_text


class Command(BaseCommand):
    help = "Export gold concepts (real lesson text) for a learning-path concept map."

    def add_arguments(self, parser):
        parser.add_argument("map_path")
        parser.add_argument("out_path")

    def handle(self, *args, map_path, out_path, **options):
        spec = json.loads(Path(map_path).read_text(encoding="utf-8"))
        ids = [object_id for concept in spec["concepts"] for object_id in concept["member_ids"]]
        objects = {item.id: item for item in LearningObject.objects.filter(id__in=ids)}
        missing = sorted(set(ids) - set(objects))
        if missing:
            raise CommandError(
                f"Learning objects not found: {missing}. Export before merging on the live database."
            )

        concepts = []
        for concept in spec["concepts"]:
            by_material = defaultdict(list)
            for object_id in concept["member_ids"]:
                by_material[objects[object_id].material_id].append(objects[object_id])
            members = []
            for material_id in sorted(by_material):
                rows = sorted(by_material[material_id], key=lambda item: (item.order, item.id))
                first = rows[0]
                members.append({
                    "title": first.title if len(rows) == 1 else concept["title"],
                    "section_title": first.section_title,
                    "content": first.content if len(rows) == 1 else merge_text(rows),
                    "material_id": material_id,
                    "order": first.order,
                })
            concepts.append({
                "key": concept["key"],
                "title": concept["title"],
                "section_title": concept.get("section_title", ""),
                "members": members,
            })

        output = {name: spec[name] for name in ("topic_id", "required", "parallel", "structural", "expected_order")}
        output["concepts"] = concepts
        Path(out_path).write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Wrote {len(concepts)} concepts to {out_path}"))
```

- [ ] **Step 3: Export the fixtures from the live database**

Run:
```
python manage.py export_gold_concepts learning_path/fixtures/gold_map_62.json learning_path/fixtures/gold_topic_62.json
python manage.py export_gold_concepts learning_path/fixtures/gold_map_79.json learning_path/fixtures/gold_topic_79.json
```
Expected: `Wrote 7 concepts to ...gold_topic_62.json` and `Wrote 9 concepts to ...gold_topic_79.json`.
Open both files and confirm the `comparing` concept of topic 62 has two members, the first starting `"Shape: Solids keep their shape"`.

- [ ] **Step 4: Write the gold report module**

Create `backend/learning_path/services/gold.py`:

```python
"""The teacher's gold-standard learning paths, and how a derivation measures up.

Gold concepts carry real lesson text exported from two uploaded lessons (see
``export_gold_concepts``). The report is what the manuscript's before/after
table quotes, and what ``test_gold_paths`` asserts on.
"""

import json
from pathlib import Path
from types import SimpleNamespace

from . import criteria
from .publishing import order_with_links

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def load_gold(topic_id):
    data = json.loads((FIXTURES / f"gold_topic_{topic_id}.json").read_text(encoding="utf-8"))
    concepts = []
    for index, row in enumerate(data["concepts"]):
        members = tuple(SimpleNamespace(**member) for member in row["members"])
        text = "\n".join(member.content for member in members)
        concepts.append(SimpleNamespace(
            id=index + 1,
            key=row["key"],
            title=row["title"],
            content=text,
            member_text=text,
            section_title=row["section_title"],
            kind="text",
            order=index,
            members=members,
        ))
    return data, concepts


def _edges(decisions, key, verdict):
    return sorted({
        (key[row["prerequisite"].id], key[row["dependent"].id])
        for row in decisions
        if row["verdict"] == verdict
    })


def gold_report(data, concepts, decisions):
    key = {concept.id: concept.key for concept in concepts}
    by_key = {concept.key: concept for concept in concepts}
    accepted = _edges(decisions, key, criteria.ACCEPTED)
    pending = _edges(decisions, key, criteria.PENDING)
    required = [tuple(edge) for edge in data["required"]]
    structural = set(data["structural"])
    parallel = [set(group) for group in data["parallel"]]

    def forbidden(edge):
        before, after = edge
        return (
            before in structural
            or after in structural
            or any(before in group and after in group for group in parallel)
            or (after, before) in required
        )

    links = [(by_key[before].id, by_key[after].id) for before, after in accepted]
    ordered, _, ignored = order_with_links(concepts, links)
    order = [key[concept.id] for concept in ordered]
    accepted_set = set(accepted)
    return {
        "topic": data["topic_id"],
        "accepted": [list(edge) for edge in accepted],
        "pending": [list(edge) for edge in pending],
        "missing_required": [list(edge) for edge in required if edge not in accepted_set],
        "forbidden_accepted": [list(edge) for edge in accepted if forbidden(edge)],
        "extra_accepted": [list(edge) for edge in accepted if edge not in required and not forbidden(edge)],
        "order": order,
        "order_matches": order == data["expected_order"],
        "ignored_links": [[key[before], key[after]] for before, after in ignored],
    }
```

- [ ] **Step 5: Write the failing gold test**

Create `backend/learning_path/test_gold_paths.py`:

```python
"""Acceptance: the two real lessons produce the teacher's learning paths.

Runs the real sentence encoder over real lesson text, so it is skipped when the
semantic models are not installed. Everything else in the learning-path suite
uses deterministic stand-ins; this is the one test that says whether the
criteria work on actual content.
"""

import json
import unittest

from django.test import SimpleTestCase

from lessons.services.semantic_grouping import SemanticUnavailable, runtime

from .services import criteria
from .services.gold import gold_report, load_gold


class GoldPathTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            cls.engine = runtime()
        except SemanticUnavailable as exc:
            raise unittest.SkipTest(f"Semantic models unavailable: {exc}")

    def _report(self, topic_id):
        data, concepts = load_gold(topic_id)
        report = gold_report(data, concepts, criteria.decide_pairs(concepts, self.engine))
        print(json.dumps(report, indent=2))
        return report

    def _assert_gold(self, report):
        self.assertEqual(report["missing_required"], [], "required edges not accepted")
        self.assertEqual(report["forbidden_accepted"], [], "forbidden edges accepted")
        self.assertTrue(report["order_matches"], f"order was {report['order']}")

    def test_solid_liquid_and_gas(self):
        self._assert_gold(self._report(62))

    def test_reproduction_among_flowering_plants(self):
        self._assert_gold(self._report(79))
```

- [ ] **Step 6: Write the evaluation command**

Create `backend/learning_path/management/commands/evaluate_gold_paths.py`:

```python
"""Print the gold report for both lessons; ``--grid`` sweeps the criteria constants.

The grid is how the constants in ``criteria.py`` were chosen: the combination
with no forbidden edges and the most required edges across both lessons, ties
going to the listed defaults. Its output is pasted into
``docs/learning_path_revision_2026-09-17.md``.
"""

import itertools
import json

from django.core.management.base import BaseCommand

from learning_path.services import criteria
from learning_path.services.gold import gold_report, load_gold
from lessons.services.semantic_grouping import runtime

TOPICS = (62, 79)
GRID = {
    "REF_MAX_DF_RATIO": (0.2, 0.34, 0.5),
    "REF_MARGIN": (0.0, 0.05, 0.1, 0.2),
    "PHRASE_COSINE": (0.7, 0.8, 0.9),
    "MIN_IOL_MARGIN": (0.1, 0.25),
}


class Command(BaseCommand):
    help = "Report derived learning paths against the gold standard."

    def add_arguments(self, parser):
        parser.add_argument("--grid", action="store_true")

    def _reports(self, engine):
        reports = []
        for topic_id in TOPICS:
            data, concepts = load_gold(topic_id)
            reports.append(gold_report(data, concepts, criteria.decide_pairs(concepts, engine)))
        return reports

    def handle(self, *args, grid=False, **options):
        engine = runtime()
        if not grid:
            self.stdout.write(json.dumps(self._reports(engine), indent=2))
            return

        names = [name for name in GRID if hasattr(criteria, name)]
        defaults = {name: getattr(criteria, name) for name in names}
        rows = []
        try:
            for values in itertools.product(*(GRID[name] for name in names)):
                for name, value in zip(names, values):
                    setattr(criteria, name, value)
                reports = self._reports(engine)
                rows.append({
                    "settings": dict(zip(names, values)),
                    "required_hits": sum(
                        len(data["required"]) - len(report["missing_required"])
                        for data, report in zip((load_gold(t)[0] for t in TOPICS), reports)
                    ),
                    "forbidden": sum(len(report["forbidden_accepted"]) for report in reports),
                    "orders_match": all(report["order_matches"] for report in reports),
                    "missing": {report["topic"]: report["missing_required"] for report in reports},
                })
        finally:
            for name, value in defaults.items():
                setattr(criteria, name, value)
        rows.sort(key=lambda row: (row["forbidden"], -row["required_hits"], not row["orders_match"]))
        self.stdout.write(json.dumps(rows[:15], indent=2))
```

(`GRID` names that do not exist yet in `criteria` are skipped, so the command works before Task 6.)

- [ ] **Step 7: Run the gold test to record the baseline**

Run: `python manage.py test learning_path.test_gold_paths -v 2`
Expected: both tests FAIL (baseline: topic 62 accepts Matter→Solid only among required; topic 79 accepts Pollination→Fertilization only). If they are skipped, stop: the semantic models are missing — run `python manage.py prepare_semantic_grouping --download` and retry.

Run: `python manage.py evaluate_gold_paths > ../docs/gold_baseline.json`

- [ ] **Step 8: Start the revision report**

Create `docs/learning_path_revision_2026-09-17.md`:

```markdown
# Learning-path criteria revision (2026-09-17)

For the manuscript's revision notes beside §2.6. Gold standard: the teacher's
prerequisite map for two uploaded lessons (`backend/learning_path/fixtures/gold_map_*.json`).

## Before (criteria v3: name-to-window SBERT similarity, cross-section cap)

| Topic | Required accepted | Forbidden accepted | Order matches |
|---|---|---|---|
| 62 Solid, Liquid and Gas | <fill from gold_baseline.json> / 10 | <fill> | <fill> |
| 79 Reproduction Among Flowering Plants | <fill> / 8 | <fill> | <fill> |
```

Replace each `<fill>` with the numbers from `docs/gold_baseline.json` (required accepted = required count minus `missing_required` length). Then delete `docs/gold_baseline.json`.

- [ ] **Step 9: Commit**

```bash
git add learning_path/fixtures learning_path/management/commands/export_gold_concepts.py learning_path/management/commands/evaluate_gold_paths.py learning_path/services/gold.py learning_path/test_gold_paths.py ../docs/learning_path_revision_2026-09-17.md
git commit -m "Add gold-standard learning paths from two real lessons

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Manual merge and split — API and UI

**Files:**
- Modify: `backend/lessons/views.py` (new actions next to `separate_learning_object`, around line 544)
- Modify: `backend/lessons/serializers.py:106-130` (`LearningObjectSerializer`)
- Modify: `frontend/src/api.js` (after `separateLearningObject`, line ~288)
- Modify: `frontend/src/pages/TopicDetailPage.jsx` (handlers near `separateObject` ~line 2240; buttons near line 2800 and line 2886)
- Test: `backend/lessons/test_object_merge.py` (append)

**Interfaces:**
- Consumes: `merge_learning_objects`, `split_learning_object`, `MergeError` (Task 1); view helpers `self._refresh_relationship_snapshots(materials, recompute=False)` and `self._learning_resources_payload(node, request)`.
- Produces:
  - `POST /api/courses/<id>/outline-nodes/<node_id>/merge-learning-objects/` body `{"learning_object_ids": [...]}` → learning-resources payload.
  - `POST /api/courses/<id>/outline-nodes/<node_id>/learning-objects/<object_id>/split/` → learning-resources payload.
  - Serializer field `merged_parts: list[{"title": str}]` on every learning object.
  - JS: `mergeLearningObjects(courseId, nodeId, ids)`, `splitLearningObject(courseId, nodeId, objectId)`.

- [ ] **Step 1: Write the failing API tests**

Append to `backend/lessons/test_object_merge.py`:

```python
from .tests import authenticated_api_client


class MergeApiTests(MergeFixture):
    def _url(self, suffix):
        return f"/api/courses/{self.course.id}/outline-nodes/{self.topic.id}/{suffix}"

    def test_merge_endpoint_merges_and_returns_the_resources(self):
        client = authenticated_api_client()

        response = client.post(
            self._url("merge-learning-objects/"),
            {"learning_object_ids": [self.shape.id, self.volume.id, self.flow.id]},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn("learning_object_groups", response.data)
        merged = LearningObject.objects.get(pk=self.shape.id)
        self.assertEqual(len(merged.merged_from), 3)
        parts = [
            item["merged_parts"]
            for group in response.data["learning_object_groups"]
            for item in group["learning_objects"]
            if item["id"] == self.shape.id
        ][0]
        self.assertEqual([part["title"] for part in parts], ["Shape", "Volume", "Flow"])

    def test_merge_endpoint_rejects_objects_from_two_pdfs(self):
        client = authenticated_api_client()

        response = client.post(
            self._url("merge-learning-objects/"),
            {"learning_object_ids": [self.shape.id, self.comparing.id]},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("same PDF", response.data["detail"])

    def test_split_endpoint_restores_the_originals(self):
        kept, _ = merge_learning_objects([self.shape, self.volume])
        client = authenticated_api_client()

        response = client.post(self._url(f"learning-objects/{kept.id}/split/"), format="json")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            sorted(self.material.learning_objects.values_list("title", flat=True)),
            ["Examples", "Flow", "Matter", "Shape", "Volume"],
        )
```

- [ ] **Step 2: Run to verify they fail**

Run: `python manage.py test lessons.test_object_merge.MergeApiTests -v 2`
Expected: FAIL with 404 status codes.

- [ ] **Step 3: Add the serializer field**

In `backend/lessons/serializers.py`, `LearningObjectSerializer`: add below `outline_node_id`:

```python
    # Titles of the originals a merged object was built from; empty otherwise.
    merged_parts = serializers.SerializerMethodField()

    def get_merged_parts(self, obj):
        return [{"title": entry.get("title", "")} for entry in (obj.merged_from or [])]
```

and add `"merged_parts"` to `Meta.fields`.

- [ ] **Step 4: Add the endpoints**

In `backend/lessons/views.py`, add the import near the other service imports:

```python
from .services.object_merge import MergeError, merge_learning_objects, split_learning_object
```

Add these actions to the same viewset as `separate_learning_object`, directly after it:

```python
    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/merge-learning-objects",
    )
    def merge_learning_objects_action(self, request, pk=None, node_id=None):
        """Merge objects from one PDF into one, for content another PDF teaches as one."""
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response({"detail": "Outline node not found."}, status=status.HTTP_404_NOT_FOUND)
        raw_ids = request.data.get("learning_object_ids")
        try:
            object_ids = {int(value) for value in raw_ids}
        except (TypeError, ValueError):
            return Response(
                {"detail": "Provide learning_object_ids as a list of integers."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        members = list(
            LearningObject.objects.filter(
                id__in=object_ids, material__outline_node=node,
            ).select_related("material")
        )
        if len(members) != len(object_ids):
            return Response(
                {"detail": "One or more learning objects were not found in this topic."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            merge_learning_objects(members)
        except MergeError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        self._refresh_relationship_snapshots({members[0].material}, recompute=False)
        return Response(self._learning_resources_payload(node, request))

    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/learning-objects/(?P<object_id>[^/.]+)/split",
    )
    def split_learning_object_action(self, request, pk=None, node_id=None, object_id=None):
        """Undo a merge: rebuild the original objects with their question links."""
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
            learning_object = LearningObject.objects.select_related("material").get(
                pk=object_id, material__outline_node=node,
            )
        except (OutlineNode.DoesNotExist, LearningObject.DoesNotExist):
            return Response({"detail": "Learning object not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            split_learning_object(learning_object)
        except MergeError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        self._refresh_relationship_snapshots({learning_object.material}, recompute=False)
        return Response(self._learning_resources_payload(node, request))
```

The URL `outline-nodes/<node_id>/learning-objects/<object_id>/split` must not collide with the existing `outline-nodes/<node_id>/learning-objects/<object_id>` route at line 1078: DRF action regexes are anchored, so it does not. Verify in Step 5.

- [ ] **Step 5: Run the API tests**

Run: `python manage.py test lessons.test_object_merge -v 2`
Expected: all tests PASS.

- [ ] **Step 6: Add the API client functions**

In `frontend/src/api.js`, after `separateLearningObject`:

```js
// Several objects from one PDF become one, when another PDF teaches them as one.
export function mergeLearningObjects(courseId, nodeId, learningObjectIds) {
  return request(
    `/courses/${courseId}/outline-nodes/${nodeId}/merge-learning-objects/`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ learning_object_ids: learningObjectIds }),
    }
  );
}

// Undo a merge; the original objects come back with their question links.
export function splitLearningObject(courseId, nodeId, learningObjectId) {
  return request(
    `/courses/${courseId}/outline-nodes/${nodeId}/learning-objects/${learningObjectId}/split/`,
    { method: "POST" }
  );
}
```

- [ ] **Step 7: Add the UI**

In `frontend/src/pages/TopicDetailPage.jsx`:

1. Add `mergeLearningObjects,` and `splitLearningObject,` to the import list from `../api` (next to `separateLearningObject` at line ~32).

2. After the `separateObject` function (~line 2253) add:

```jsx
  const allLearningObjects = (resources?.learning_object_groups || []).flatMap(
    (group) => group.learning_objects || [],
  );
  const selectedMaterials = new Set(
    allLearningObjects
      .filter((item) => selectedIds.includes(item.id))
      .map((item) => Number(item.material)),
  );
  const canMergeSelected = selectedIds.length >= 2 && selectedMaterials.size === 1;

  async function mergeSelected() {
    if (reviewStep !== "objects" || !canMergeSelected) return;
    setBusyAction("merge");
    onError("");
    onMessage("");
    try {
      const data = await mergeLearningObjects(courseId, topicId, selectedIds);
      setResources(data);
      setSelectedIds([]);
      onMessage("Selected objects were merged into one. Use “Split back” to undo.");
    } catch (err) {
      onError(err.message);
    } finally {
      setBusyAction("");
    }
  }

  async function splitObject(item) {
    if (reviewStep !== "objects") return;
    setBusyAction(`split-${item.id}`);
    onError("");
    onMessage("");
    try {
      const data = await splitLearningObject(courseId, topicId, item.id);
      setResources(data);
      onMessage(`“${item.title}” was split back into ${item.merged_parts.length} objects.`);
    } catch (err) {
      onError(err.message);
    } finally {
      setBusyAction("");
    }
  }
```

3. Directly before the "Connect selected objects" `<button>` (~line 2886) add:

```jsx
          <button
            type="button"
            className="btn btn-secondary"
            disabled={!canMergeSelected || Boolean(busyAction)}
            title={canMergeSelected ? "" : "Select two or more objects from the same PDF"}
            onClick={mergeSelected}
          >
            {busyAction === "merge" ? "Merging…" : "Merge into one"}
          </button>
```

4. In the object row, directly after the `{isConnected && reviewStep === "objects" && (...Separate...)}` block (~line 2809) add:

```jsx
                            {item.merged_parts?.length > 0 && reviewStep === "objects" && (
                              <button
                                type="button"
                                className="btn btn-secondary btn-small"
                                disabled={Boolean(busyAction)}
                                title={`Merged from: ${item.merged_parts.map((part) => part.title).join(", ")}`}
                                onClick={() => splitObject(item)}
                              >
                                {busyAction === `split-${item.id}` ? "Splitting…" : "Split back"}
                              </button>
                            )}
```

- [ ] **Step 8: Verify the frontend compiles**

Run (from `C:\MAVIA\frontend`): `npm run build`
Expected: `✓ built in ...` with no errors. Do not commit `frontend/dist` in this task.

- [ ] **Step 9: Commit**

```bash
git add lessons/views.py lessons/serializers.py lessons/test_object_merge.py ../frontend/src/api.js ../frontend/src/pages/TopicDetailPage.jsx
git commit -m "Add merge into one and split back to the grouping step

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Heading-matched unit suggestions

**Files:**
- Create: `backend/lessons/services/unit_matching.py`
- Modify: `backend/lessons/services/learning_resource_linker.py` (end of `refresh_learning_object_match_suggestions`, line ~946)
- Modify: `backend/lessons/views.py:700-752` (`accept_match_suggestion`)
- Modify: `backend/lessons/serializers.py:133-150` (`LearningObjectMatchSuggestionSerializer`)
- Modify: `frontend/src/pages/TopicDetailPage.jsx:335-380` (suggestion card)
- Test: `backend/lessons/test_unit_matching.py`

**Interfaces:**
- Consumes: `merge_learning_objects`, `choose_kept_row`, `merge_text`, `MergeError` (Task 1); `semantic_grouping._singular_label`, `semantic_grouping.runtime`, `semantic_grouping.policy`, `semantic_grouping.SemanticUnavailable`; `learning_resource_linker.normalize_learning_object_title`.
- Produces:
  - `heading_key(text: str) -> str`
  - `Unit(label: str, members: tuple[LearningObject, ...])` with `.material_id`, `.ids`
  - `find_units(objects: list[LearningObject]) -> list[Unit]`
  - `heading_unit_candidates(node) -> list[dict]` — each `{"label", "left": [LearningObject], "right": [LearningObject]}`
  - `refresh_heading_unit_suggestions(node, runtime_instance=None) -> int` (number of pending unit suggestions)
  - Serializer fields `source_members`, `candidate_members` (lists of serialized objects, kept row first).

- [ ] **Step 1: Write the failing tests**

Create `backend/lessons/test_unit_matching.py`:

```python
"""Units: several objects of one PDF that another PDF teaches as one.

Scores alone cannot find them (measured: every item scored 0.5-0.65 against
every section), and structure alone over-merges (a "Matter" section holding
Matter, Solid, Liquid and Gas). A unit is proposed only when a heading in the
other PDF names it, and never merged without the teacher.
"""

import os
from unittest.mock import patch

from django.test import TestCase

from .models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    LearningObjectMatchSuggestion,
    OutlineNode,
)
from .services.unit_matching import (
    find_units,
    heading_key,
    heading_unit_candidates,
    refresh_heading_unit_suggestions,
)
from .test_semantic_grouping import FakeRuntime
from .tests import authenticated_api_client


class UnitFixture(TestCase):
    """Shaped like topic 62: material A itemises, material B uses sections."""

    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="States")
        confirmed = {"learning_objects_confirmed": True}
        self.a = LearningMaterial.objects.create(course=self.course, outline_node=self.topic, title="A", generated_json=dict(confirmed))
        self.b = LearningMaterial.objects.create(course=self.course, outline_node=self.topic, title="B", generated_json=dict(confirmed))
        o = self._object
        self.matter = o(self.a, "Matter", "Matter", 0)
        self.solid_a = o(self.a, "Solid", "Matter", 1)
        self.liquid_a = o(self.a, "Liquid", "Matter", 2)
        self.shape = o(self.a, "Shape", "Comparing the Three States", 3)
        self.volume = o(self.a, "Volume", "Comparing the Three States", 4)
        self.examples_a = o(self.a, "Everyday Examples", "", 5)
        self.solids_b = o(self.b, "Solids", "Solids", 0)
        self.diagram_b = o(self.b, "Diagram description", "Solids", 1)
        self.liquids_b = o(self.b, "Liquids", "Liquids", 2)
        self.compare_b = o(self.b, "Comparing the Three States", "Comparing the Three States", 3)
        self.table_b = o(self.b, "5. Comparing the Three States", "", 4, kind="image")
        self.changing_b = o(self.b, "Changing From One State to Another", "", 5)
        self.changing_fig = o(self.b, "6. Changing From One State to Another", "", 6, kind="image")
        self.examples_b = o(self.b, "Everyday Examples", "Everyday Examples", 7)
        self.examples_table = o(self.b, "7. Everyday Examples", "", 8, kind="image")

    def _object(self, material, title, section, order, kind="text"):
        group = LearningObjectGroup.objects.create(outline_node=self.topic, label=title)
        return LearningObject.objects.create(
            material=material, group=group, title=title, content=f"{title} text.",
            section_title=section, order=order, kind=kind,
        )

    def _labels(self, candidates):
        return {
            (c["label"], tuple(item.id for item in c["left"]), tuple(item.id for item in c["right"]))
            for c in candidates
        }


class HeadingKeyTests(UnitFixture):
    def test_numbering_case_and_plural_fold_away(self):
        self.assertEqual(heading_key("5. Comparing the Three States"), "comparing the three state")
        self.assertEqual(heading_key("Solids"), heading_key("Solid"))


class FindUnitTests(UnitFixture):
    def test_units_follow_shared_sections_and_repeated_titles(self):
        units_b = {unit.label: unit.ids for unit in find_units(list(self.b.learning_objects.all()))}

        self.assertEqual(units_b[heading_key("Solids")], [self.solids_b.id, self.diagram_b.id])
        self.assertEqual(units_b[heading_key("Comparing the Three States")], [self.compare_b.id, self.table_b.id])
        self.assertEqual(units_b[heading_key("Changing From One State to Another")], [self.changing_b.id, self.changing_fig.id])
        self.assertEqual(units_b[heading_key("Everyday Examples")], [self.examples_b.id, self.examples_table.id])
        self.assertNotIn(heading_key("Liquids"), units_b)

    def test_a_section_of_itemised_concepts_is_still_a_unit(self):
        units_a = {unit.label: unit.ids for unit in find_units(list(self.a.learning_objects.all()))}

        self.assertEqual(units_a["matter"], [self.matter.id, self.solid_a.id, self.liquid_a.id])


class CandidateTests(UnitFixture):
    def test_units_are_proposed_only_where_the_other_pdf_names_them(self):
        labels = self._labels(heading_unit_candidates(self.topic))

        self.assertIn(
            (heading_key("Comparing the Three States"), (self.shape.id, self.volume.id), (self.compare_b.id, self.table_b.id)),
            labels,
        )
        self.assertIn(("solid", (self.solid_a.id,), (self.solids_b.id, self.diagram_b.id)), labels)
        self.assertIn(
            (heading_key("Everyday Examples"), (self.examples_a.id,), (self.examples_b.id, self.examples_table.id)),
            labels,
        )
        self.assertFalse(any(label == "matter" for label, _, _ in labels))
        self.assertFalse(any(label == heading_key("Changing From One State to Another") for label, _, _ in labels))

    def test_a_unit_whose_members_already_agree_across_pdfs_is_not_proposed(self):
        self.solids_b.group = self.solid_a.group
        self.solids_b.save(update_fields=["group"])
        self.diagram_b.group = self.liquid_a.group
        self.diagram_b.save(update_fields=["group"])

        labels = self._labels(heading_unit_candidates(self.topic))

        self.assertFalse(any(label == "solid" for label, _, _ in labels))

    def test_an_ambiguous_label_is_not_proposed(self):
        self._object(self.a, "Solid", "", 9)

        labels = self._labels(heading_unit_candidates(self.topic))

        self.assertFalse(any(label == "solid" for label, _, _ in labels))


SEMANTIC_ENV = {
    "SEMANTIC_GROUPING_MODE": "auto",
    "SEMANTIC_GROUPING_CALIBRATION": "",
    "SEMANTIC_GROUPING_AUTO_THRESHOLD": "",
    "SEMANTIC_GROUPING_REVIEW_THRESHOLD": "",
    "SEMANTIC_GROUPING_MINIMUM_SBERT_COSINE": "",
    "SEMANTIC_GROUPING_MINIMUM_MARGIN": "",
}


class SuggestionTests(UnitFixture):
    def _refresh(self, score=0.5):
        runtime = FakeRuntime()
        runtime.pair_scores = lambda pairs: [score for _ in pairs]
        with patch.dict(os.environ, SEMANTIC_ENV):
            return refresh_heading_unit_suggestions(self.topic, runtime_instance=runtime)

    def test_suggestions_are_pending_and_carry_every_member(self):
        self._refresh()

        suggestion = LearningObjectMatchSuggestion.objects.get(
            evidence__label=heading_key("Comparing the Three States"),
        )
        self.assertEqual(suggestion.status, LearningObjectMatchSuggestion.Status.PENDING)
        self.assertEqual(suggestion.evidence["method"], "heading_unit_v1")
        members = {suggestion.source_learning_object_id, suggestion.candidate_learning_object_id,
                   *suggestion.source_extra_ids, *suggestion.candidate_extra_ids}
        self.assertEqual(members, {self.shape.id, self.volume.id, self.compare_b.id, self.table_b.id})

    def test_a_score_below_the_review_threshold_proposes_nothing(self):
        self._refresh(score=0.1)

        self.assertFalse(LearningObjectMatchSuggestion.objects.exists())

    def test_even_a_high_score_never_merges_automatically(self):
        self._refresh(score=0.99)

        self.assertEqual(self.a.learning_objects.count(), 6)
        self.assertFalse(
            LearningObjectMatchSuggestion.objects.exclude(
                status=LearningObjectMatchSuggestion.Status.PENDING,
            ).exists()
        )

    def test_a_rejected_unit_suggestion_is_not_reopened(self):
        self._refresh()
        LearningObjectMatchSuggestion.objects.update(status=LearningObjectMatchSuggestion.Status.REJECTED)

        self._refresh()

        self.assertFalse(
            LearningObjectMatchSuggestion.objects.filter(status=LearningObjectMatchSuggestion.Status.PENDING).exists()
        )

    def test_accepting_merges_both_sides_and_connects_them(self):
        self._refresh()
        suggestion = LearningObjectMatchSuggestion.objects.get(
            evidence__label=heading_key("Comparing the Three States"),
        )
        client = authenticated_api_client()

        response = client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.topic.id}/match-suggestions/{suggestion.id}/accept/",
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        merged_a = LearningObject.objects.get(pk=self.shape.id)
        merged_b = LearningObject.objects.get(pk=self.compare_b.id)
        self.assertEqual(merged_a.title, "Comparing the Three States")
        self.assertEqual(len(merged_a.merged_from), 2)
        self.assertEqual(len(merged_b.merged_from), 2)
        self.assertEqual(merged_a.group_id, merged_b.group_id)
        self.assertFalse(LearningObject.objects.filter(pk__in=[self.volume.id, self.table_b.id]).exists())
```

- [ ] **Step 2: Run to verify they fail**

Run: `python manage.py test lessons.test_unit_matching -v 2`
Expected: ERROR, `No module named 'lessons.services.unit_matching'`.

- [ ] **Step 3: Implement unit matching**

Create `backend/lessons/services/unit_matching.py`:

```python
"""Propose merging several objects of one PDF that another PDF teaches as one.

Measured on topic 62 before this was written: the cross-encoder scored nearly
every item of one PDF between 0.5 and 0.65 against every section of the other,
so scores cannot find these units. Document structure finds candidates -- runs
of consecutive objects under one heading -- but on its own it over-merges: a
"Matter" section may hold Matter, Solid, Liquid and Gas. A unit is therefore
proposed only when the *other* PDF names it with a matching heading or title,
the cross-encoder confirms at the review threshold, and the teacher accepts.
"""

from collections import defaultdict
from dataclasses import dataclass

from ..models import LearningObject, LearningObjectMatchSuggestion
from .learning_resource_linker import normalize_learning_object_title
from .object_merge import choose_kept_row, merge_text
from . import semantic_grouping

METHOD = "heading_unit_v1"


def heading_key(text):
    """A heading or title reduced to what two PDFs would share."""
    return semantic_grouping._singular_label(normalize_learning_object_title(text or ""))


@dataclass(frozen=True)
class Unit:
    label: str
    members: tuple

    @property
    def material_id(self):
        return self.members[0].material_id

    @property
    def ids(self):
        return [item.id for item in self.members]


def find_units(objects):
    """Runs of two or more consecutive objects under one heading.

    A run continues while the next object sits under the same section heading,
    or carries that heading as its own title -- extraction sometimes drops the
    section of a figure ("5. Comparing the Three States") but keeps its title.
    """
    ordered = sorted(objects, key=lambda item: (item.order, item.id))
    units, index = [], 0
    while index < len(ordered):
        first = ordered[index]
        label = heading_key(first.section_title) or heading_key(first.title)
        end = index + 1
        while label and end < len(ordered) and label in (
            heading_key(ordered[end].section_title),
            heading_key(ordered[end].title),
        ):
            end += 1
        if end - index >= 2:
            units.append(Unit(label, tuple(ordered[index:end])))
            index = end
        else:
            index += 1
    return units


def _agrees_across_pdfs(unit):
    """Two or more members already connected to other PDFs: they agree at a finer grain."""
    connected = [
        item for item in unit.members
        if item.group_id and LearningObject.objects.filter(group_id=item.group_id)
        .exclude(material_id=item.material_id).exists()
    ]
    return len(connected) >= 2


def heading_unit_candidates(node):
    """``[{"label", "left": [...], "right": [...]}]`` -- unit pairs worth scoring."""
    objects = list(
        LearningObject.objects.filter(
            material__outline_node=node,
            material__generated_json__learning_objects_confirmed=True,
        ).select_related("material")
    )
    by_material = defaultdict(list)
    for item in objects:
        by_material[item.material_id].append(item)
    all_units = {material_id: find_units(rows) for material_id, rows in by_material.items()}
    open_units = {
        material_id: [unit for unit in units if not _agrees_across_pdfs(unit)]
        for material_id, units in all_units.items()
    }

    candidates, seen = [], set()
    for left_id, units in open_units.items():
        for right_id, rows in by_material.items():
            if right_id == left_id:
                continue
            for unit in units:
                same_label_members = {
                    item.id for other in all_units[right_id] if other.label == unit.label
                    for item in other.members
                }
                singles = [
                    item for item in rows
                    if heading_key(item.title) == unit.label and item.id not in same_label_members
                ]
                others = [other for other in open_units[right_id] if other.label == unit.label]
                if len(singles) + len(others) != 1:
                    continue
                right = list(others[0].members) if others else singles
                pair = frozenset((*unit.ids, *(item.id for item in right)))
                if pair in seen:
                    continue
                seen.add(pair)
                left, right_rows = (list(unit.members), right)
                if left_id > right_id:
                    left, right_rows = right_rows, left
                candidates.append({"label": unit.label, "left": left, "right": right_rows})
    return candidates


def refresh_heading_unit_suggestions(node, runtime_instance=None):
    """Create or refresh pending unit suggestions for this topic. Never merges."""
    engine = runtime_instance or semantic_grouping.runtime()
    threshold = semantic_grouping.policy()["review_threshold"]
    created = 0
    for candidate in heading_unit_candidates(node):
        left, right = candidate["left"], candidate["right"]
        try:
            kept_left, kept_right = choose_kept_row(left), choose_kept_row(right)
            score = engine.pair_scores([(merge_text(left), merge_text(right))])[0]
        except Exception:  # noqa: BLE001 -- MergeError or model limits: no suggestion
            continue
        if score < threshold:
            continue
        source, candidate_row = (kept_left, kept_right) if kept_left.id < kept_right.id else (kept_right, kept_left)
        source_side, candidate_side = (left, right) if source is kept_left else (right, left)
        source_extra = sorted(item.id for item in source_side if item.id != source.id)
        candidate_extra = sorted(item.id for item in candidate_side if item.id != candidate_row.id)

        existing = LearningObjectMatchSuggestion.objects.filter(
            source_learning_object=source, candidate_learning_object=candidate_row,
        ).first()
        if (
            existing
            and existing.status == LearningObjectMatchSuggestion.Status.REJECTED
            and existing.source_extra_ids == source_extra
            and existing.candidate_extra_ids == candidate_extra
        ):
            continue
        LearningObjectMatchSuggestion.objects.update_or_create(
            source_learning_object=source,
            candidate_learning_object=candidate_row,
            defaults={
                "outline_node": node,
                "similarity_score": round(score, 6),
                "confidence": LearningObjectMatchSuggestion.Confidence.MEDIUM,
                "status": LearningObjectMatchSuggestion.Status.PENDING,
                "source_extra_ids": source_extra,
                "candidate_extra_ids": candidate_extra,
                "evidence": {
                    "method": METHOD,
                    "label": candidate["label"],
                    "score": round(score, 6),
                    "source_ids": [item.id for item in source_side],
                    "candidate_ids": [item.id for item in candidate_side],
                    "title_used": True,
                },
            },
        )
        created += 1
    return created
```

Note on the import cycle: `learning_resource_linker` must import `unit_matching` *inside* the function (Step 5), never at module top.

- [ ] **Step 4: Run the service tests**

Run: `python manage.py test lessons.test_unit_matching -v 2`
Expected: all tests PASS except `test_accepting_merges_both_sides_and_connects_them` (FAIL: volume still exists).

If `test_a_section_of_itemised_concepts_is_still_a_unit` or the candidate tests fail on the fixture's `Everyday Examples` object with empty section: `find_units` treats `examples_a` (section `""`) as its own label from its title; that is intended — it only forms a unit with a following object titled the same.

- [ ] **Step 5: Hook into the refresh**

In `backend/lessons/services/learning_resource_linker.py`, at the very end of `refresh_learning_object_match_suggestions` (after the `grouping_warning` cleanup block, ~line 946), add:

```python
    if semantic_active:
        from .unit_matching import refresh_heading_unit_suggestions
        try:
            refresh_heading_unit_suggestions(material.outline_node)
        except Exception:  # noqa: BLE001 -- unit proposals must never break grouping
            logger.exception("Heading-matched unit suggestions could not be refreshed")
```

The `stale_pending` deletion earlier in the function removes pending unit suggestions touching this material; this call recreates them from current structure, so the queue stays current.

- [ ] **Step 6: Merge extras on accept**

In `backend/lessons/views.py`, `accept_match_suggestion`, replace the lines from `source = suggestion.source_learning_object` through `suggestion.save(update_fields=["status", "updated_at"])` with:

```python
        source = suggestion.source_learning_object
        candidate = suggestion.candidate_learning_object
        if not all(
            learning_objects_are_confirmed(item.material)
            for item in (source, candidate)
        ):
            return Response(
                {"detail": "Confirm both learning objects before reviewing this connection."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if suggestion.source_extra_ids or suggestion.candidate_extra_ids:
            try:
                source, candidate = self._merge_suggestion_units(suggestion, source, candidate)
            except MergeError as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
            suggestion = LearningObjectMatchSuggestion.objects.get(
                source_learning_object=source, candidate_learning_object=candidate,
            )
        suggestion.status = LearningObjectMatchSuggestion.Status.ACCEPTED
        suggestion.save(update_fields=["status", "updated_at"])
```

and add this helper method to the same viewset (above `accept_match_suggestion`):

```python
    @transaction.atomic
    def _merge_suggestion_units(self, suggestion, source, candidate):
        """Merge each side of a unit suggestion; return the two kept rows.

        The suggestion row cascades away if one of its FK rows is merged away,
        so the pair is recorded again against the kept rows.
        """
        sides = []
        for primary, extra_ids in (
            (source, suggestion.source_extra_ids),
            (candidate, suggestion.candidate_extra_ids),
        ):
            members = [primary, *LearningObject.objects.filter(
                pk__in=extra_ids, material_id=primary.material_id,
            )]
            if len(members) != 1 + len(extra_ids):
                raise MergeError("This suggestion is out of date. Refresh the page and review it again.")
            sides.append(members)

        fields = {
            "outline_node": suggestion.outline_node,
            "similarity_score": suggestion.similarity_score,
            "confidence": suggestion.confidence,
            "evidence": suggestion.evidence,
            "status": suggestion.status,
        }
        kept = []
        for members in sides:
            if len(members) == 1:
                kept.append(members[0])
            else:
                row, _ = merge_learning_objects(members)
                kept.append(row)
        left, right = sorted(kept, key=lambda item: item.id)
        LearningObjectMatchSuggestion.objects.update_or_create(
            source_learning_object=left, candidate_learning_object=right, defaults=fields,
        )
        return left, right
```

Add `from django.db import transaction` to the imports of `views.py` if it is not already imported. `MergeError` and `merge_learning_objects` are already imported (Task 3).

- [ ] **Step 7: Expose unit members in the serializer**

In `backend/lessons/serializers.py`, `LearningObjectMatchSuggestionSerializer`:

```python
    source_members = serializers.SerializerMethodField()
    candidate_members = serializers.SerializerMethodField()

    def _members(self, primary, extra_ids):
        extras = LearningObject.objects.filter(pk__in=extra_ids).order_by("order", "id")
        return LearningObjectSerializer([primary, *extras], many=True, context=self.context).data

    def get_source_members(self, obj):
        return self._members(obj.source_learning_object, obj.source_extra_ids)

    def get_candidate_members(self, obj):
        return self._members(obj.candidate_learning_object, obj.candidate_extra_ids)
```

Add `"source_members", "candidate_members", "source_extra_ids", "candidate_extra_ids"` to `Meta.fields`. Import `LearningObject` from `.models` if the serializer module does not already.

- [ ] **Step 8: Run the tests**

Run: `python manage.py test lessons.test_unit_matching lessons.test_object_merge -v 2`
Expected: all PASS.

Run: `python manage.py test lessons -v 1`
Expected: no new failures compared with Task 1 Step 7. The refresh hook now runs in existing grouping tests (their `FakeRuntime` scores 0.95). If an existing test fails only because an extra `heading_unit_v1` suggestion appeared, confirm its fixture really has a heading-matched unit across two PDFs, then narrow that test's assertion to non-unit suggestions (`.exclude(evidence__method="heading_unit_v1")`) with a `# Changed 2026-09-17` comment. Any other failure is a regression to fix.

- [ ] **Step 9: Show every member on the suggestion card**

In `frontend/src/pages/TopicDetailPage.jsx`, inside the `match-suggestion-card` (~lines 352-375), replace each side's `<strong>{source.title}</strong> ... <p className="match-source-content">...</p>` block with a list over the members. For side A:

```jsx
                      {(suggestion.source_members?.length ? suggestion.source_members : [source]).map((member) => (
                        <div key={member.id} className="match-unit-member">
                          <strong>{member.title}</strong>
                          {member.image_url && (
                            <img className="review-source-image" src={member.image_url} alt={member.title || "Source A"} />
                          )}
                          <p className="match-source-content">{member.content || "No narration content."}</p>
                        </div>
                      ))}
```

Side B is identical with `candidate_members`, `candidate` and `"Source B"`. Above the pair, when `suggestion.source_extra_ids?.length || suggestion.candidate_extra_ids?.length`, render:

```jsx
                  {(suggestion.source_extra_ids?.length > 0 || suggestion.candidate_extra_ids?.length > 0) && (
                    <p className="muted-text">
                      Accepting merges the objects on each side into one, then connects them. You can split them back later.
                    </p>
                  )}
```

Run (from `C:\MAVIA\frontend`): `npm run build` — expected: builds with no errors.

- [ ] **Step 10: Commit**

```bash
git add lessons/services/unit_matching.py lessons/services/learning_resource_linker.py lessons/views.py lessons/serializers.py lessons/test_unit_matching.py ../frontend/src/pages/TopicDetailPage.jsx
git commit -m "Propose heading-matched unit merges across PDFs

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Concept surface — names, Examples, member text

**Files:**
- Modify: `backend/learning_path/services/concepts.py`
- Modify: `backend/learning_path/services/text_signals.py:31` (`MAX_CONCEPT_TITLE_WORDS`)
- Modify: `backend/learning_path/services/concept_units.py` (`Concept`, `concepts_for_topic`)
- Modify: `backend/learning_path/services/criteria.py` (`decide_pairs`)
- Modify: `backend/learning_path/services/publishing.py` (`order_with_links`)
- Test: `backend/learning_path/test_concepts.py`, `backend/learning_path/test_publishing.py`, `backend/learning_path/test_criteria.py` (append)

**Interfaces:**
- Produces:
  - `concepts.strip_numbering(title: str) -> str`
  - `concepts.is_structural(learning_object) -> bool` (reads `.title`)
  - `Concept.member_text: str` — members' contents joined with newlines in document scan order.
  - `decide_pairs` never returns a pair involving a structural concept.
  - `order_with_links` places structural concepts after all others.

- [ ] **Step 1: Write the failing tests**

Append to `backend/learning_path/test_concepts.py`:

```python
from .services.concepts import is_structural, strip_numbering


class Headed(Chunk):
    def __init__(self, title, content="", section_title=""):
        super().__init__(title, content)
        self.section_title = section_title


class NumberingAndStructureTests(SimpleTestCase):
    def test_leading_numbers_are_stripped(self):
        self.assertEqual(strip_numbering("7. Everyday Examples"), "Everyday Examples")
        self.assertEqual(strip_numbering("5) Comparing"), "Comparing")
        self.assertEqual(strip_numbering("Solid"), "Solid")

    def test_a_numbered_generic_label_is_structural_and_unnamed(self):
        """Regression: "7. Everyday Examples" escaped the label check and took
        13 accepted edges on topic 62."""
        chunk = Chunk("7. Everyday Examples", "Ice, water and steam.")
        self.assertTrue(is_structural(chunk))
        self.assertIsNone(resolve_concept(chunk))

    def test_a_six_word_heading_is_a_name(self):
        """"Changing From One State to Another" was unnameable at four words."""
        chunk = Chunk("Changing From One State to Another", "Heat changes states.")
        self.assertEqual(resolve_concept(chunk), "changing from one state to another")

    def test_a_long_title_falls_back_to_its_heading(self):
        chunk = Headed(
            "Here is what the table below shows about every state of matter",
            "It lists shape and volume.",
            section_title="3. Comparing the Three States",
        )
        self.assertEqual(resolve_concept(chunk), "comparing the three states")
```

Append to `backend/learning_path/test_publishing.py` (it already imports `order_with_links`; if not, add `from .services.publishing import order_with_links`):

```python
from types import SimpleNamespace


class StructuralOrderTests(TestCase):
    def test_examples_come_last_even_when_the_document_puts_them_first(self):
        concepts = [
            SimpleNamespace(id=1, title="Everyday Examples"),
            SimpleNamespace(id=2, title="Solid"),
            SimpleNamespace(id=3, title="Gas"),
        ]

        ordered, _, _ = order_with_links(concepts, [])

        self.assertEqual([concept.id for concept in ordered], [2, 3, 1])
```

Append to `backend/learning_path/test_criteria.py` inside `DecidePairTests`:

```python
    def test_structural_concepts_take_part_in_no_pair(self):
        examples = self._concept("7. Everyday Examples", "Matter, a solid, a liquid and a gas.", 5)
        concepts = concepts_for_topic(self.topic)

        decisions = decide_pairs(concepts, KeywordRuntime())

        self.assertFalse(any(
            examples.id in (row["prerequisite"].id, row["dependent"].id) for row in decisions
        ))

    def test_member_text_joins_every_pdf(self):
        other = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="Other", status="completed",
        )
        solid = self.by_title["Solid"]
        LearningObject.objects.create(
            material=other, group=solid.group, title="Solids",
            content="Solid particles vibrate.", order=0,
        )
        concept = next(c for c in concepts_for_topic(self.topic) if c.id == solid.id)

        self.assertIn("A solid is matter that keeps its shape.", concept.member_text)
        self.assertIn("Solid particles vibrate.", concept.member_text)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python manage.py test learning_path.test_concepts learning_path.test_publishing learning_path.test_criteria -v 2`
Expected: ImportError for `is_structural` / `strip_numbering` (collection error), which fails the new tests.

- [ ] **Step 3: Implement names and structure**

In `backend/learning_path/services/text_signals.py` change:

```python
# A title only names a concept when it is short. Long titles in this corpus are
# full sentences lifted from the PDF ("Matter is anything that has mass...").
# Six, not four: "Changing From One State to Another" is a heading, and at four
# words that concept could never be referred to.
MAX_CONCEPT_TITLE_WORDS = 6
```

In `backend/learning_path/services/concepts.py`:

- Add `import re` at the top.
- Add `"example"` to `STRUCTURAL_LABELS`.
- Add after `STRUCTURAL_LABELS`:

```python
_LEADING_NUMBER = re.compile(r"^\s*\d+(?:\.\d+)*\s*[.):-]*\s*")


def strip_numbering(title):
    """Drop a heading's list number: "7. Everyday Examples" -> "Everyday Examples"."""
    return _LEADING_NUMBER.sub("", title or "").strip()


def is_structural(learning_object):
    """True for document furniture ("Everyday Examples") that orders last with no edges."""
    title = strip_numbering(strip_part_suffix(learning_object.title or ""))
    return normalize(title) in STRUCTURAL_LABELS


def _heading_name(heading):
    """A concept name taken from the heading a passage sits under, or ``None``."""
    normalized = normalize(strip_numbering(heading or ""))
    if not normalized or normalized in STRUCTURAL_LABELS:
        return None
    words = normalized.split()
    significant = [
        word for word in words
        if word not in STOP_WORDS and len(word) >= MIN_TERM_LENGTH
    ]
    if significant and len(words) <= MAX_CONCEPT_TITLE_WORDS:
        return normalized
    return None
```

- In `resolve_concept`, change the first two lines to:

```python
    title = strip_numbering(strip_part_suffix(learning_object.title or ""))
    normalized = normalize(title)
```

- In `resolve_concept`, directly before the final `subject = definition_subject(learning_object.content or "")`, add:

```python
    # A long title names nothing, but the heading it sits under may.
    heading = _heading_name(getattr(learning_object, "section_title", ""))
    if heading:
        return heading
```

- [ ] **Step 4: Implement member text**

In `backend/learning_path/services/concept_units.py`:

- Add to the `Concept` dataclass, after `order: int`:

```python
    # Every member's text, one PDF after another. The criteria read this so a
    # concept speaks with all its PDFs' wording, not only the Normal version's.
    member_text: str = ""
```

(Place it before the fields that have `field(...)` defaults so dataclass ordering stays valid; it has a default, so it may sit after `order`.)

- In `concepts_for_topic`, inside the loop before `concepts.append(`, add:

```python
        scan = sorted(members, key=lambda item: _member_scan_key(item, material_rank))
        member_text = "\n".join(item.content or "" for item in scan)
```

and pass `member_text=member_text,` to `Concept(...)`.

- [ ] **Step 5: Exclude structural concepts from pairs and order them last**

In `backend/learning_path/services/criteria.py`:

- Change `from .concepts import resolve_concept` to `from .concepts import is_structural, resolve_concept`.
- In `decide_pairs`, replace `concepts = list(concepts)` with:

```python
    # Examples and similar furniture present concepts; nothing depends on them
    # and they depend on nothing. They are ordered last by `order_with_links`.
    concepts = [concept for concept in concepts if not is_structural(concept)]
```

In `backend/learning_path/services/publishing.py`:

- Add `from .concepts import is_structural`.
- In `order_with_links`, after `position = {...}` add:

```python
    structural = {concept.id for concept in concepts if is_structural(concept)}

    def rank(concept_id):
        # Structural concepts ("Everyday Examples") always close the path.
        return (concept_id in structural, position[concept_id])
```

- Replace `chosen = min(ready, key=position.get)` with `chosen = min(ready, key=rank)` and replace the loop's `key=position.get` in the cycle branch with `key=rank`.

- [ ] **Step 6: Run the learning-path suite**

Run: `python manage.py test learning_path -v 2`
Expected: new tests PASS. For any pre-existing test that now fails, decide:
- It asserts a 5- or 6-word title is unnameable, or that a numbered generic label is nameable → deliberately changed. Update the assertion and add a one-line comment `# Changed 2026-09-17: <reason>`.
- Anything else → a regression; fix the implementation, not the test.

`GoldPathTests` still fails (expected until Task 7).

- [ ] **Step 7: Commit**

```bash
git add learning_path/services/concepts.py learning_path/services/text_signals.py learning_path/services/concept_units.py learning_path/services/criteria.py learning_path/services/publishing.py learning_path/test_concepts.py learning_path/test_publishing.py learning_path/test_criteria.py
git commit -m "Name numbered and long headings; order Examples last with no edges

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: RefD-style semantic reference

**Files:**
- Modify: `backend/learning_path/services/criteria.py`
- Test: `backend/learning_path/test_criteria.py`

**Interfaces:**
- Consumes: `Concept.member_text`, `is_structural` (Task 5); `text_signals.normalize`, `singular`, `mentions`, `STOP_WORDS`, `MIN_TERM_LENGTH`.
- Produces (all in `criteria`):
  - Constants `REF_MAX_DF_RATIO = 0.34`, `REF_MARGIN = 0.05`, `PHRASE_COSINE = 0.80` (module-level, patched by `evaluate_gold_paths --grid`).
  - `concept_text(concept) -> str`
  - `key_terms(concepts) -> dict[int, dict[str, float]]`
  - `reference_details(concepts, runtime_instance=None) -> tuple[dict[tuple[int,int], float], dict[tuple[int,int], list[str]]]` — `matrix[(a, b)]` = how strongly b's text refers to a; `matched[(a, b)]` = a's key terms found in b.
  - `reference_matrix(concepts, runtime_instance=None)` — unchanged signature, returns the matrix only.
  - Evidence keys: `ref_forward`, `ref_backward`, `ref_margin`, `terms_forward`, `terms_backward`, `iol_prerequisite`, `iol_dependent`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/learning_path/test_criteria.py`:

```python
from .services.criteria import key_terms, reference_details


def plant_concepts():
    """Stamen, Pistil and Pollination among enough neighbours that their terms
    count as distinctive (six concepts: a term may appear in two)."""
    return [
        Stub(1, order=0, title="Stamen", content="The stamen has an anther and a filament. The anther makes pollen."),
        Stub(2, order=1, title="Pistil", content="The pistil has a stigma, a style and an ovary."),
        Stub(3, order=2, title="Pollination", content="Pollen moves from the anther to the stigma."),
        Stub(4, order=3, title="Petals", content="Petals attract bees with colour."),
        Stub(5, order=4, title="Seed", content="A seed holds a tiny plant and stored food."),
        Stub(6, order=5, title="Fruit", content="A fruit protects seeds and helps spread them."),
    ]


class KeyTermTests(TestCase):
    def test_a_concept_owns_the_terms_it_introduces(self):
        terms = key_terms(plant_concepts())

        self.assertIn("anther", terms[1])
        self.assertIn("pollen", terms[1])
        self.assertIn("stigma", terms[2])
        self.assertIn("stamen", terms[1])

    def test_terms_every_concept_uses_belong_to_none(self):
        concepts = [
            Stub(index, order=index, title=title, content=f"{title} is made of particles.")
            for index, title in enumerate(("Solid", "Liquid", "Gas", "Plasma"), start=1)
        ]

        terms = key_terms(concepts)

        self.assertFalse(any("particle" in weights for weights in terms.values()))


class RefDReferenceTests(TestCase):
    def test_using_a_concepts_terms_is_a_reference_to_it(self):
        """Regression, topic 79: Stamen -> Pollination scored backwards under
        name-to-window similarity (0.23 forward, 0.32 backward)."""
        concepts = plant_concepts()
        stamen, pollination = concepts[0], concepts[2]

        matrix, matched = reference_details(concepts, KeywordRuntime())

        self.assertGreater(matrix[(1, 3)], matrix[(3, 1)])
        self.assertIn("anther", matched[(1, 3)])
        self.assertEqual(semantic_reference(stamen, pollination, matrix), 1)
        self.assertEqual(semantic_reference(pollination, stamen, matrix), 0)

    def test_a_comparison_is_not_referenced_by_its_parts(self):
        """Regression, topic 62: every comparative sentence looked like a
        reference to "Comparing the Three States"."""
        shape = Stub(1, order=0, title="Shape", content="Solids keep their shape; liquids take the shape of the container.")
        comparing = Stub(2, order=1, title="Comparing the Three States", content="The table lists shape, volume and flow for each state.")

        matrix, _ = reference_details([shape, comparing], KeywordRuntime())

        self.assertEqual(semantic_reference(comparing, shape, matrix), 0)


class ThanContrastTests(TestCase):
    def test_a_comparison_with_than_is_contrastive(self):
        self.assertTrue(only_contrastive_mentions("solid", "Particles have more energy than in a solid."))
```

In the existing `CastVoteTests` (~line 217-229), replace the three `csr_*` assertions with:

```python
        # Renamed 2026-09-17: reference is RefD key-term share, not SBERT cosine.
        self.assertEqual(result["ref_forward"], 0.4)
        self.assertEqual(result["ref_backward"], 0.1)
        self.assertAlmostEqual(result["ref_margin"], 0.3)
```

Replace `test_an_edge_across_sections_is_never_accepted_automatically` with:

```python
    def test_crossing_sections_is_recorded_but_no_longer_caps_the_verdict(self):
        """Changed 2026-09-17: the cap's evidence (35 wrong cross-section edges)
        came from per-state Examples and diagrams, now excluded or merged; the
        teacher's gold edge Solid -> Comparing crosses sections."""
        same, _ = self._verdicts_with_sections("States", "States")
        crossing, _ = self._verdicts_with_sections("Solids", "Liquids")

        self.assertFalse(same["cross_section"])
        self.assertTrue(crossing["cross_section"])
        self.assertEqual(crossing["verdict"], same["verdict"])
```

- [ ] **Step 2: Run to verify they fail**

Run: `python manage.py test learning_path.test_criteria -v 2`
Expected: ImportError for `key_terms` / `reference_details`.

- [ ] **Step 3: Implement RefD reference**

In `backend/learning_path/services/criteria.py`:

- Replace the imports block with:

```python
import math
import re
from collections import Counter, defaultdict

from lessons.services.semantic_grouping import runtime as semantic_runtime

from .concepts import is_structural, resolve_concept
from .text_signals import MIN_TERM_LENGTH, STOP_WORDS, mentions, normalize, singular
```

- Add after `MIN_IOL_MARGIN`:

```python
# RefD (Liang et al., 2015): A is a prerequisite of B when B refers to A more
# than A refers to B. A concept is referred to through its *key terms* -- its
# name plus the terms it introduces. The previous measure compared the
# embedding of A's name with B's text, which is similarity, not reference:
# measured on two real lessons, 7 of 8 misses were reversed.

# A term used by more than this share of a topic's concepts belongs to none of
# them ("particles" in a states-of-matter lesson).
REF_MAX_DF_RATIO = 0.34

# How much more B must refer to A than A to B for the vote to count.
REF_MARGIN = 0.05

# ACE (Aytekin & Saygin, 2024) link: a multi-word name phrased differently
# ("change of state") still counts when a text window is this close to it.
PHRASE_COSINE = 0.80
```

- Replace `reference_matrix` (the whole function and its docstring) with:

```python
def concept_text(concept):
    """All of a concept's wording: every member PDF when known, else its own text."""
    return getattr(concept, "member_text", "") or concept.content or ""


def _terms(text):
    return [
        singular(word) for word in normalize(text).split()
        if word not in STOP_WORDS and len(word) >= MIN_TERM_LENGTH
    ]


def key_terms(concepts):
    """``{concept id: {term: weight}}`` -- what a concept can be referred to by.

    A distinctive term (used by at most ``REF_MAX_DF_RATIO`` of the concepts)
    belongs to the concept that *introduces* it -- the earliest concept using
    it -- and weighs its inverse document frequency. Ownership by densest use
    was rejected: "The anther makes pollen" introduces pollen, but Pollination
    uses it more densely, which made Stamen -> Pollination a tie. Words of any concept's name are left
    to the name itself. The name weighs as much as all the concept's terms
    together: naming a concept is the plainest reference to it.
    """
    concepts = list(concepts)
    names = concept_names(concepts)
    name_words = {
        singular(word) for name in names.values() if name for word in name.split()
    }
    counts = {concept.id: Counter(_terms(concept_text(concept))) for concept in concepts}
    order = {concept.id: concept.order for concept in concepts}
    total = len(concepts)
    frequency = Counter(term for bag in counts.values() for term in bag)
    limit = max(1, math.floor(total * REF_MAX_DF_RATIO))

    weights = {concept.id: {} for concept in concepts}
    for term, document_frequency in frequency.items():
        if document_frequency > limit or term in name_words:
            continue
        owner = min(
            (concept_id for concept_id, bag in counts.items() if bag[term]),
            key=lambda concept_id: (order[concept_id], concept_id),
        )
        weights[owner][term] = math.log(total / document_frequency) if total > 1 else 1.0

    for concept in concepts:
        name = names[concept.id]
        if name:
            weights[concept.id][name] = max(sum(weights[concept.id].values()), 1.0)
    return {concept_id: terms for concept_id, terms in weights.items() if terms}


def _phrase_hits(concepts, phrases, texts, runtime_instance):
    """``{(holder id, phrase)}`` -- multi-word names a text says in other words."""
    engine = runtime_instance or semantic_runtime()
    windowed = {concept.id: _windows(concept_text(concept)) for concept in concepts}
    payload = list(phrases)
    for concept in concepts:
        payload.extend(windowed[concept.id])
    vectors = engine.embeddings(payload)
    phrase_vectors = dict(zip(phrases, vectors[:len(phrases)]))

    hits, cursor = set(), len(phrases)
    for concept in concepts:
        count = len(windowed[concept.id])
        window_vectors = vectors[cursor:cursor + count]
        cursor += count
        for phrase, phrase_vector in phrase_vectors.items():
            if mentions(texts[concept.id], phrase):
                continue
            if any(
                sum(left * right for left, right in zip(phrase_vector, window)) >= PHRASE_COSINE
                for window in window_vectors
            ):
                hits.add((concept.id, phrase))
    return hits


def reference_details(concepts, runtime_instance=None):
    """``(matrix, matched)`` -- ``matrix[(a, b)]`` is how strongly b's text refers to a.

    The score is the weighted share of a's key terms that b's text uses. Only
    concepts with key terms get rows: a concept that owns nothing cannot be
    referred to, so no comparison involving it is a measurement.
    """
    concepts = list(concepts)
    terms = key_terms(concepts)
    names = concept_names(concepts)
    texts = {concept.id: normalize(concept_text(concept)) for concept in concepts}
    phrases = sorted({
        names[concept_id] for concept_id in terms
        if names.get(concept_id) and len(names[concept_id].split()) > 1
    })
    phrase_hits = _phrase_hits(concepts, phrases, texts, runtime_instance) if phrases else set()

    matrix, matched = {}, {}
    for target_id, weights in terms.items():
        total = sum(weights.values())
        for holder in concepts:
            if holder.id == target_id:
                continue
            found = [
                term for term in weights
                if mentions(texts[holder.id], term) or (holder.id, term) in phrase_hits
            ]
            matrix[(target_id, holder.id)] = sum(weights[term] for term in found) / total
            matched[(target_id, holder.id)] = sorted(found)
    return matrix, matched


def reference_matrix(concepts, runtime_instance=None):
    """``{(a, b): score}`` -- how strongly b's text refers to a. See ``reference_details``."""
    return reference_details(concepts, runtime_instance)[0]
```

- In `inbound_outbound_ratios`, update the docstring's first bold paragraph to say "**Only concepts with key terms get a ratio.**" (the code is unchanged: it already reads rows from the matrix).

- In `semantic_reference`, replace the last line with:

```python
    return 1 if forward - backward > REF_MARGIN else 0
```

- Replace `cast_votes` with:

```python
def cast_votes(a, b, matrix, ratios, matched=None):
    """Every criterion's vote for "a comes before b", plus the numbers behind it."""
    matched = matched or {}
    forward = matrix.get((a.id, b.id), 0.0)
    backward = matrix.get((b.id, a.id), 0.0)
    return {
        "temporal_order": temporal_order(a, b),
        "semantic_reference": semantic_reference(a, b, matrix),
        "inbound_outbound": inbound_outbound(a, b, ratios),
        "ref_forward": round(forward, 6),
        "ref_backward": round(backward, 6),
        "ref_margin": round(forward - backward, 6),
        "terms_forward": matched.get((a.id, b.id), []),
        "terms_backward": matched.get((b.id, a.id), []),
        "iol_prerequisite": round(ratios.get(a.id, 0.0), 6),
        "iol_dependent": round(ratios.get(b.id, 0.0), 6),
    }
```

- Change `_CONTRAST` to:

```python
_CONTRAST = re.compile(r"\b(while|whereas|unlike|but not|although|however|than)\b", re.I)
```

and add to the KNOWN LIMITATION comment above it: `"than" added 2026-09-17: "more energy than in a solid" compares, it does not build on solids.`

- In `decide_pairs`: replace `matrix = reference_matrix(concepts, runtime_instance)` with `matrix, matched = reference_details(concepts, runtime_instance)`; replace `votes = cast_votes(a, b, matrix, ratios)` with `votes = cast_votes(a, b, matrix, ratios, matched)`; delete the two lines

```python
            if cross_section and verdict == ACCEPTED:
                verdict = PENDING
```

and replace the docstring's bold paragraph with: `The cross-section flag is recorded for the teacher but no longer caps a verdict: see learning_path/CRITERIA.md (revision 2026-09-17).`

- [ ] **Step 4: Run the criteria tests**

Run: `python manage.py test learning_path.test_criteria -v 2`
Expected: new tests PASS. For each other failing test, apply the Task 5 Step 6 rule: update only tests that pin replaced behaviour (name-window cosine values, SBERT-only reference, cross-section cap), each with `# Changed 2026-09-17: <reason>`; otherwise fix the code. List every changed test in the commit message body.

- [ ] **Step 5: Run the whole learning-path suite**

Run: `python manage.py test learning_path -v 2`
Expected: all PASS except `GoldPathTests` (handled in Task 7).

- [ ] **Step 6: Update CRITERIA.md**

Append to `backend/learning_path/CRITERIA.md`:

```markdown
## Revision 2026-09-17: RefD key-term reference

Measured on two uploaded lessons against the teacher's gold map
(`fixtures/gold_map_*.json`), the name-to-window SBERT similarity voted
backwards on 7 of 8 missed edges, and the cross-section cap blocked correct
edges such as Solid -> Comparing.

- Semantic reference is now RefD-style: the weighted share of A's key terms
  (its name plus distinctive terms it introduces) used by B's text. SBERT
  matches multi-word names phrased differently (`PHRASE_COSINE`).
- Foundationality is unchanged but reads the new reference scores.
- Structural concepts (Examples) take part in no pair and are ordered last.
- "than" clauses count as contrastive.
- The cross-section cap is removed; `cross_section` is still stored.

Before/after numbers: `docs/learning_path_revision_2026-09-17.md`.
```

- [ ] **Step 7: Commit**

```bash
git add learning_path/services/criteria.py learning_path/test_criteria.py learning_path/CRITERIA.md
git commit -m "Measure semantic reference by RefD key terms instead of name similarity

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Calibrate against the gold standard

**Files:**
- Modify: `backend/learning_path/services/criteria.py` (constants only)
- Modify: `docs/learning_path_revision_2026-09-17.md`

**Interfaces:**
- Consumes: `evaluate_gold_paths [--grid]` (Task 2), criteria constants (Task 6).

- [ ] **Step 1: Run the gold report with defaults**

Run: `python manage.py evaluate_gold_paths`
Record per topic: `missing_required`, `forbidden_accepted`, `order_matches`.

- [ ] **Step 2: Run the grid**

Run: `python manage.py evaluate_gold_paths --grid`
Expected: up to 15 rows sorted by forbidden (ascending), required hits (descending), orders matching.

- [ ] **Step 3: Choose constants**

Pick the first row with `forbidden == 0`. If several rows tie on `required_hits` and `orders_match`, pick the one closest to the defaults (`REF_MAX_DF_RATIO=0.34, REF_MARGIN=0.05, PHRASE_COSINE=0.8, MIN_IOL_MARGIN=0.25`). Set those values in `criteria.py`, each with a comment `# Calibrated 2026-09-17 on gold topics 62 and 79; see docs/learning_path_revision_2026-09-17.md`.

- [ ] **Step 4: Apply the stop condition**

Run: `python manage.py test learning_path.test_gold_paths -v 2`

- If both tests PASS → continue to Step 5.
- If any required edge is still missing, or no row has `forbidden == 0`: **STOP.** Do not add lesson-specific rules, word lists, or thresholds tuned to a single edge. Report to the user: the missing edges, their `terms_forward`/`terms_backward` and `ref_*` evidence from `python manage.py evaluate_gold_paths`, and the best grid row. Wait for the user's decision before continuing.

- [ ] **Step 5: Run the full backend suite**

Run: `python manage.py test learning_path lessons course -v 1`
Expected: no failures beyond those already failing before Task 1 (compare with Task 1 Step 7).

- [ ] **Step 6: Complete the report**

Append to `docs/learning_path_revision_2026-09-17.md`:

```markdown
## After (RefD key-term reference, Examples structural, no cross-section cap)

Constants: REF_MAX_DF_RATIO = <value>, REF_MARGIN = <value>, PHRASE_COSINE = <value>, MIN_IOL_MARGIN = <value>

| Topic | Required accepted | Forbidden accepted | Order matches |
|---|---|---|---|
| 62 Solid, Liquid and Gas | <n> / 10 | <n> | <yes/no> |
| 79 Reproduction Among Flowering Plants | <n> / 8 | <n> | <yes/no> |

Extra accepted edges (not required, not forbidden): <list from extra_accepted>

Caveat: two lessons; constants may overfit. Re-check when a third lesson is available.
```

Replace every `<...>` with values from `python manage.py evaluate_gold_paths`.

- [ ] **Step 7: Commit**

```bash
git add learning_path/services/criteria.py ../docs/learning_path_revision_2026-09-17.md
git commit -m "Calibrate learning-path criteria on the gold standard

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Media cleanup

**Files:**
- Create: `backend/lessons/signals.py`
- Modify: `backend/lessons/apps.py`
- Modify: `backend/config/settings.py:123`
- Create: `backend/lessons/management/commands/clean_orphan_media.py`
- Test: `backend/lessons/test_media_cleanup.py`

**Interfaces:**
- Produces: post-delete file removal for `LearningMaterial.pdf_file` and `CourseOutline.outline_file`; command `clean_orphan_media [--delete]`.

- [ ] **Step 1: Write the failing tests**

Create `backend/lessons/test_media_cleanup.py`:

```python
"""Stored PDFs leave with their rows.

Measured 2026-09-17: media/learning_materials held about 390 files for 4 live
materials -- rows deleted by cascade (a course or topic) never removed their
files, and tests wrote into the real media folder.
"""

from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase

from .models import CourseGroup, LearningMaterial


class MediaCleanupTests(TestCase):
    def _material(self, course):
        material = LearningMaterial(course=course, title="Doc")
        material.pdf_file.save("doc.pdf", ContentFile(b"%PDF-1.4"), save=False)
        material.save()
        return material

    def test_tests_do_not_write_into_the_project_media_folder(self):
        self.assertNotEqual(Path(settings.MEDIA_ROOT), Path(settings.BASE_DIR) / "media")

    def test_a_cascade_delete_removes_the_file(self):
        course = CourseGroup.objects.create(title="Science")
        material = self._material(course)
        path = Path(material.pdf_file.path)
        self.assertTrue(path.exists())

        with self.captureOnCommitCallbacks(execute=True):
            course.delete()

        self.assertFalse(path.exists())

    def test_orphans_are_listed_and_only_deleted_on_request(self):
        course = CourseGroup.objects.create(title="Science")
        kept = self._material(course)
        orphan = Path(settings.MEDIA_ROOT) / "learning_materials" / "orphan.pdf"
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_bytes(b"%PDF-1.4")

        out = StringIO()
        call_command("clean_orphan_media", stdout=out)
        self.assertIn("orphan.pdf", out.getvalue())
        self.assertTrue(orphan.exists())

        call_command("clean_orphan_media", "--delete", stdout=StringIO())
        self.assertFalse(orphan.exists())
        self.assertTrue(Path(kept.pdf_file.path).exists())
```

- [ ] **Step 2: Run to verify they fail**

Run: `python manage.py test lessons.test_media_cleanup -v 2`
Expected: 3 FAIL (MEDIA_ROOT is the project folder; file remains; command unknown).

- [ ] **Step 3: Use a temporary media folder under tests**

In `backend/config/settings.py`, add `import sys` and `import tempfile` to the imports, and replace line 123 `MEDIA_ROOT = BASE_DIR / "media"` with:

```python
MEDIA_ROOT = BASE_DIR / "media"
if len(sys.argv) > 1 and sys.argv[1] == "test":
    # Tests upload files; they must never land in the real media folder.
    MEDIA_ROOT = Path(tempfile.mkdtemp(prefix="mavia-test-media-"))
```

- [ ] **Step 4: Delete files with their rows**

Create `backend/lessons/signals.py`:

```python
"""Remove stored files when their rows are deleted, including by cascade.

Deleting a course or topic cascades to its materials without calling their
views, so file removal has to hang off the row itself. It runs after commit:
a rolled-back delete must keep its file.
"""

from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import CourseOutline, LearningMaterial


def _delete_after_commit(field_file):
    if not field_file or not field_file.name:
        return
    storage, name = field_file.storage, field_file.name
    transaction.on_commit(lambda: storage.exists(name) and storage.delete(name))


@receiver(post_delete, sender=LearningMaterial)
def delete_material_file(sender, instance, **kwargs):
    _delete_after_commit(instance.pdf_file)


@receiver(post_delete, sender=CourseOutline)
def delete_outline_file(sender, instance, **kwargs):
    _delete_after_commit(instance.outline_file)
```

In `backend/lessons/apps.py` add to `LessonsConfig`:

```python
    def ready(self):
        from . import signals  # noqa: F401 -- registers file cleanup receivers
```

- [ ] **Step 5: Write the orphan command**

Create `backend/lessons/management/commands/clean_orphan_media.py`:

```python
"""List (and with --delete, remove) media files no row refers to."""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from lessons.models import CourseOutline, LearningMaterial

FOLDERS = ("learning_materials", "outlines")


class Command(BaseCommand):
    help = "Find uploaded files that no LearningMaterial or CourseOutline refers to."

    def add_arguments(self, parser):
        parser.add_argument("--delete", action="store_true", help="Delete the orphaned files.")

    def handle(self, *args, delete=False, **options):
        root = Path(settings.MEDIA_ROOT)
        referenced = {
            (root / name).resolve()
            for name in [
                *LearningMaterial.objects.exclude(pdf_file="").values_list("pdf_file", flat=True),
                *CourseOutline.objects.exclude(outline_file="").values_list("outline_file", flat=True),
            ]
        }
        orphans = [
            path for folder in FOLDERS if (root / folder).is_dir()
            for path in sorted((root / folder).iterdir())
            if path.is_file() and path.resolve() not in referenced
        ]
        for path in orphans:
            self.stdout.write(str(path.relative_to(root)))
            if delete:
                path.unlink()
        verb = "Deleted" if delete else "Found"
        self.stdout.write(self.style.SUCCESS(f"{verb} {len(orphans)} orphaned file(s)."))
```

- [ ] **Step 6: Run the tests**

Run: `python manage.py test lessons.test_media_cleanup -v 2`
Expected: 3 PASS.

- [ ] **Step 7: Report orphans in the real media folder (no deletion)**

Run: `python manage.py clean_orphan_media`
Expected: a list ending `Found N orphaned file(s).` with N around 380. Do **not** run `--delete`; tell the user the count and let them run it.

- [ ] **Step 8: Commit**

```bash
git add lessons/signals.py lessons/apps.py config/settings.py lessons/management/commands/clean_orphan_media.py lessons/test_media_cleanup.py
git commit -m "Delete uploaded files with their rows and report orphaned media

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Live re-run on both lessons

This task changes the user's database. **Ask the user before Step 1** and do the UI steps with them (the teacher accepts merges).

**Files:**
- Modify: `docs/learning_path_revision_2026-09-17.md`
- Modify (rebuild): `frontend/dist/`

- [ ] **Step 1: Back up the database**

Run: `cp db.sqlite3 db.sqlite3.pre-merge-rerun.20260917`
Expected: file exists with the same size as `db.sqlite3`.

- [ ] **Step 2: Refresh suggestions for topic 62**

Run:
```
python manage.py shell -c "from lessons.models import LearningMaterial as M; from lessons.services.learning_resource_linker import refresh_learning_object_match_suggestions as r; [r(m) for m in M.objects.filter(outline_node_id=62)]; from lessons.models import LearningObjectMatchSuggestion as S; print(list(S.objects.filter(outline_node_id=62, evidence__method='heading_unit_v1').values_list('evidence__label', flat=True)))"
```
Expected labels include `comparing the three state`, `solid`, `liquid`, `gas`, `everyday example`; not `matter`.

- [ ] **Step 3: Teacher actions in the UI (topic 62, grouping step)**

With the user, in the topic review step 1:
1. Separate the wrong figure pair ("Okay, let's describe this figure…" / "6. Changing From One State to Another").
2. Accept the unit suggestions for Comparing, Solid, Liquid, Gas and Everyday Examples.
3. Tick "Changing From One State to Another", "6. Changing From One State to Another" and "Example" (material 16) → **Merge into one**.
4. Tick "Matter usually exists…" and "As a general rule" (material 16) → **Merge into one**; tick the particles figure and "Matter" (material 15) → **Merge into one**; then connect the two Matter objects with **Connect selected objects**.
5. Republish the topic.

- [ ] **Step 4: Teacher actions in the UI (topic 79)**

1. Separate the flower-figure pair (138 / 150).
2. Material 13: merge the flower figure with "Reproduction in Flowering Plants". Material 14: merge "Reproduction in Flowering Plants (Part 1 of 2)", "(Part 2 of 2)" and the figure description.
3. Republish.

- [ ] **Step 5: Inspect the published paths**

Run: `python manage.py show_learning_path` (for topics 62 and 79)
Compare with the gold orders in the spec §3. Record accepted edges and order in `docs/learning_path_revision_2026-09-17.md` under a new heading `## Live re-run`. If the live result differs from the gold report of Task 7, record the difference and its cause (for example, a merge the teacher did differently).

- [ ] **Step 6: Rebuild the frontend bundle**

Run (from `C:\MAVIA\frontend`): `npm run build`

- [ ] **Step 7: Commit**

```bash
git add ../docs/learning_path_revision_2026-09-17.md ../frontend/dist
git commit -m "Record live re-run of both lessons and rebuild frontend

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7b: Head-word and section-containment reference (amendment)

Added 2026-09-17 after Task 7 stopped (spec §6). Run before re-running Task 7.

**Files:**
- Modify: `backend/learning_path/services/criteria.py` (`reference_details`, new helpers)
- Modify: `backend/learning_path/services/concepts.py` (public `heading_name`)
- Test: `backend/learning_path/test_criteria.py`

**Interfaces:**
- Consumes: `key_terms`, `concept_names`, `concept_text`, `mentions`, `singular`, `STOP_WORDS`, `MIN_TERM_LENGTH`; `concepts._heading_name`.
- Produces:
  - `concepts.heading_name(heading: str) -> str | None` (public wrapper of `_heading_name`).
  - `criteria.head_words(names: dict[int, str | None]) -> dict[int, str]` — unambiguous head word per concept with a multi-word name.
  - `criteria.contained_in(holder, target_name: str | None) -> bool` — any member of `holder` (or `holder` itself when it has no `members`) has `heading_name(section_title) == target_name`.
  - `reference_details` keeps its `(matrix, matched)` shapes; containment sets `matrix[(a, b)] = 1.0` with `matched[(a, b)] == ["section:<name>"]`; head-word matches add `"head:<word>"` to `matched`.

- [ ] **Step 1: Write the failing tests**

Add `head_words` to the `from .services.criteria import (...)` block at the top of `backend/learning_path/test_criteria.py`, then append:

```python
class Headed:
    """A stub concept whose members carry section headings."""

    def __init__(self, id, order, title, content, sections=("",)):
        self.id = id
        self.order = order
        self.title = title
        self.content = content
        self.member_text = content
        self.section_title = sections[0]
        self.kind = "text"
        self.members = tuple(
            type("Member", (), {"section_title": section, "title": title, "content": content})()
            for section in sections
        )


class HeadWordTests(TestCase):
    def test_the_head_word_is_the_first_significant_word(self):
        names = {1: "seed formation", 2: "stamen male part", 3: "solid"}

        self.assertEqual(head_words(names), {1: "seed", 2: "stamen"})

    def test_a_shared_head_word_is_ambiguous(self):
        names = {1: "seed formation", 2: "seed dispersal"}

        self.assertEqual(head_words(names), {})

    def test_saying_the_head_word_refers_to_a_multi_word_concept(self):
        """Regression, topic 79: Fruit says "seed", never "seed formation"."""
        seed = Stub(1, order=0, title="Seed formation", content="The ovule becomes a seed with stored food.")
        fruit = Stub(2, order=1, title="Fruit formation", content="The ovary grows around the seed and ripens.")
        others = [
            Stub(3, order=2, title="Petals", content="Petals attract bees with colour."),
            Stub(4, order=3, title="Roots", content="Roots take in water from soil."),
        ]

        matrix, matched = reference_details([seed, fruit, *others], KeywordRuntime())

        self.assertIn("head:seed", matched[(1, 2)])
        self.assertEqual(semantic_reference(seed, fruit, matrix), 1)


class SectionContainmentTests(TestCase):
    def test_a_concept_under_anothers_heading_refers_to_it(self):
        """Regression, topic 62: Solid sits under the "Matter" heading but never
        says "matter"; the overview names solid, so RefD read it backwards."""
        matter = Headed(1, 0, "Matter", "Matter has mass. It can be a solid, a liquid or a gas.", ("Matter",))
        solid = Headed(2, 1, "Solid", "A solid keeps its shape.", ("Matter", "Solids"))
        others = [
            Headed(3, 2, "Roots", "Roots take in water."),
            Headed(4, 3, "Leaves", "Leaves make food."),
        ]

        matrix, matched = reference_details([matter, solid, *others], KeywordRuntime())

        self.assertEqual(matrix[(1, 2)], 1.0)
        self.assertEqual(matched[(1, 2)], ["section:matter"])
        self.assertEqual(semantic_reference(matter, solid, matrix), 1)
        self.assertEqual(semantic_reference(solid, matter, matrix), 0)

    def test_a_concept_never_contains_itself(self):
        matter = Headed(1, 0, "Matter", "Matter has mass.", ("Matter",))
        roots = Headed(2, 1, "Roots", "Roots take in water.")

        matrix, matched = reference_details([matter, roots], KeywordRuntime())

        self.assertNotIn((1, 1), matrix)
        self.assertFalse(any(term.startswith("section:") for terms in matched.values() for term in terms))
```

- [ ] **Step 2: Run to verify they fail**

Run: `python manage.py test learning_path.test_criteria -v 2`
Expected: ImportError for `head_words`.

- [ ] **Step 3: Implement**

In `backend/learning_path/services/concepts.py`, after `_heading_name`, add:

```python
def heading_name(heading):
    """What a section heading names, or ``None``. Public for the criteria."""
    return _heading_name(heading)
```

In `backend/learning_path/services/criteria.py`:

- Change the concepts import to `from .concepts import heading_name, is_structural, resolve_concept`.
- Add after `concept_text`:

```python
def head_words(names):
    """``{concept id: head word}`` for multi-word names, where unambiguous.

    Lessons name a concept by its whole heading ("seed formation") but refer
    to it by its first significant word ("the seed"). A head word that two
    names share points at neither, so it never counts.
    """
    candidates = {}
    for concept_id, name in names.items():
        if not name or len(name.split()) < 2:
            continue
        for word in name.split():
            if word not in STOP_WORDS and len(word) >= MIN_TERM_LENGTH:
                candidates[concept_id] = singular(word)
                break
    counts = Counter(candidates.values())
    return {concept_id: word for concept_id, word in candidates.items() if counts[word] == 1}


def contained_in(holder, target_name):
    """True when any of ``holder``'s members sits under a heading naming the target.

    Wang et al. (2016) read textbook section structure as prerequisite
    evidence: a passage under the "Matter" heading builds on Matter even when
    it never says "matter".
    """
    if not target_name:
        return False
    members = getattr(holder, "members", None) or (holder,)
    return any(
        heading_name(getattr(member, "section_title", "") or "") == target_name
        for member in members
    )
```

- In `reference_details`, after the `phrase_hits = ...` line add `heads = head_words(names)`, and replace the body of the inner `for holder in concepts:` loop (after the `if holder.id == target_id: continue`) with:

```python
            if contained_in(holder, names.get(target_id)):
                matrix[(target_id, holder.id)] = 1.0
                matched[(target_id, holder.id)] = [f"section:{names[target_id]}"]
                continue
            found, score = [], 0.0
            for term, weight in weights.items():
                if mentions(texts[holder.id], term) or (holder.id, term) in phrase_hits:
                    found.append(term)
                    score += weight
                elif (
                    term == names.get(target_id)
                    and target_id in heads
                    and mentions(texts[holder.id], heads[target_id])
                ):
                    found.append(f"head:{heads[target_id]}")
                    score += weight
            matrix[(target_id, holder.id)] = score / total
            matched[(target_id, holder.id)] = sorted(found)
```

- [ ] **Step 4: Run the learning-path suite**

Run: `python manage.py test learning_path -v 2`
Expected: new tests PASS; only `GoldPathTests` may fail. Apply the Task 5 Step 6 rule to any other failure.

- [ ] **Step 5: Measure**

Run: `python manage.py evaluate_gold_paths` and record per topic: required hits, forbidden, order.

- [ ] **Step 6: Commit**

```bash
git add learning_path/services/criteria.py learning_path/services/concepts.py learning_path/test_criteria.py
git commit -m "Count head-word mentions and section containment as reference

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

Then re-run Task 7 from Step 1.
