# Learning Object Versions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every learning object three versions — original, simplified, elaborated — sourced from real teacher-written text where semantic grouping found it, and from the LLM only where it did not.

**Architecture:** A group's earliest-uploaded member becomes the representative and supplies the original. Its partners' texts are classified into simplified/elaborated by a readability heuristic that defers to the teacher when the signal is thin. Gemma fills only slots real material cannot. Absorbed members keep their rows and question links, marked by a `represented_by` flag, so an uncalibrated grouping decision stays reversible.

**Tech Stack:** Django 5.2, Django REST Framework, SQLite, Ollama (Gemma) via `requests`. No new third-party dependencies — Flesch-Kincaid is implemented locally.

**Spec:** `docs/superpowers/specs/2026-09-10-learning-object-versions-design.md`

## Global Constraints

- Python is invoked as `..\.venv\Scripts\python.exe` from `backend/`. Never `python`; the global interpreter is a different environment.
- Tests run with `..\.venv\Scripts\python.exe manage.py test <label> --noinput` from `backend/`.
- No new pip dependencies. Flesch-Kincaid and syllable counting are implemented in-repo.
- Existing behaviour that must not regress: `manage.py test lessons course` currently passes 187 tests. `learning_path` is NOT in `INSTALLED_APPS` and must stay out — it is out of scope.
- Slot values are the strings `"SIMPLIFIED"`, `"ELABORATED"`, `"EXTRA"`. The `original` is never stored as a row; it is the representative's own `content`.
- Thresholds are operating values, not calibrated: `FK_MARGIN = 1.5`, `WORD_RATIO = 1.25`, `WORD_DELTA = 10`.
- Never delete a `LessonVariant` whose `origin` is `"source_pdf"`. Human-written text is not regenerable.
- The adaptive escalation ladder in `backend/adaptive/services.py` is out of scope. Do not modify it.

---

### Task 1: Readability heuristic

Pure functions, no Django imports, no database. This is the piece the whole design leans on, so it lands first and alone.

**Files:**
- Create: `backend/course/readability.py`
- Test: `backend/course/test_readability.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `syllable_count(word: str) -> int`
  - `flesch_kincaid_grade(text: str) -> float`
  - `text_metrics(text: str) -> dict` with keys `words: int`, `sentences: int`, `fk: float`
  - `compare(original_text: str, candidate_text: str) -> dict` with keys `slot: str` (`"SIMPLIFIED"` or `"ELABORATED"`), `confident: bool`, `delta_fk: float`, `delta_words: int`, `ratio: float`, `agree: bool`
  - Constants `FK_MARGIN`, `WORD_RATIO`, `WORD_DELTA`

- [ ] **Step 1: Write the failing test**

Create `backend/course/test_readability.py`:

```python
from django.test import SimpleTestCase

from .readability import compare, flesch_kincaid_grade, syllable_count, text_metrics


SHORT = "Solid has a fixed shape. It holds its form. It does not flow."
LONG = (
    "A solid is a state of matter that maintains a fixed shape and a fixed volume. "
    "The particles inside it are packed tightly together in a regular arrangement. "
    "Because those particles cannot move past one another, a solid does not flow."
)


class SyllableCountTests(SimpleTestCase):
    def test_counts_single_vowel_group_as_one(self):
        self.assertEqual(syllable_count("solid"), 2)

    def test_silent_e_is_not_counted(self):
        self.assertEqual(syllable_count("shape"), 1)

    def test_every_word_has_at_least_one_syllable(self):
        self.assertEqual(syllable_count("rhythm"), 1)

    def test_punctuation_is_ignored(self):
        self.assertEqual(syllable_count("solid,"), 2)


class TextMetricsTests(SimpleTestCase):
    def test_counts_words_and_sentences(self):
        metrics = text_metrics(SHORT)
        self.assertEqual(metrics["words"], 13)
        self.assertEqual(metrics["sentences"], 3)

    def test_empty_text_does_not_divide_by_zero(self):
        metrics = text_metrics("")
        self.assertEqual(metrics["words"], 0)
        self.assertIsInstance(metrics["fk"], float)


class FleschKincaidTests(SimpleTestCase):
    def test_longer_denser_text_scores_higher(self):
        self.assertGreater(flesch_kincaid_grade(LONG), flesch_kincaid_grade(SHORT))


class CompareTests(SimpleTestCase):
    def test_clearly_longer_candidate_is_elaborated_and_confident(self):
        result = compare(SHORT, LONG)
        self.assertEqual(result["slot"], "ELABORATED")
        self.assertTrue(result["confident"])
        self.assertTrue(result["agree"])

    def test_clearly_shorter_candidate_is_simplified_and_confident(self):
        result = compare(LONG, SHORT)
        self.assertEqual(result["slot"], "SIMPLIFIED")
        self.assertTrue(result["confident"])

    def test_near_identical_texts_are_not_confident(self):
        other = "Solid keeps a fixed shape. It holds its form. It will not flow."
        result = compare(SHORT, other)
        self.assertFalse(result["confident"])

    def test_disagreeing_signals_are_never_confident(self):
        # More words, but far shorter sentences and simpler vocabulary:
        # word count says "elaborated", Flesch-Kincaid says "simplified".
        wordy_but_simple = "It is a solid. It has a shape. The shape stays. It is not a gas. It does not flow at all."
        dense_but_short = (
            "Solids demonstrate characteristically incompressible volumetric permanence "
            "alongside structurally invariant morphological configuration."
        )
        result = compare(dense_but_short, wordy_but_simple)
        self.assertFalse(result["agree"])
        self.assertFalse(result["confident"])

    def test_still_proposes_a_slot_when_not_confident(self):
        other = "Solid keeps a fixed shape. It holds its form. It will not flow."
        result = compare(SHORT, other)
        self.assertIn(result["slot"], ("SIMPLIFIED", "ELABORATED"))

    def test_reports_the_margins_it_used(self):
        result = compare(SHORT, LONG)
        self.assertGreater(result["delta_fk"], 0)
        self.assertGreater(result["delta_words"], 0)
        self.assertGreaterEqual(result["ratio"], 1.0)
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `backend/`:
```
..\.venv\Scripts\python.exe manage.py test course.test_readability --noinput
```
Expected: FAIL — `ModuleNotFoundError: No module named 'course.readability'`

- [ ] **Step 3: Write the implementation**

Create `backend/course/readability.py`:

```python
"""Relative readability comparison for two texts teaching the same concept.

This is deliberately not a classifier. It orders two passages and reports how
confident that ordering is, so an unconvincing comparison can be handed to a
teacher instead of guessed at. Thresholds are operating values chosen to be
conservative; they are not calibrated probabilities.
"""

import re


# A comparison is only automatic when every margin clears and both signals
# point the same way. See the design doc for the measurements behind these.
FK_MARGIN = 1.5
WORD_RATIO = 1.25
WORD_DELTA = 10

_VOWELS = "aeiouy"
_SENTENCE_SPLIT = re.compile(r"[.!?]+")
_NON_ALPHA = re.compile(r"[^a-z]")


def syllable_count(word):
    """Count vowel groups, discounting a silent trailing 'e'.

    An approximation. It is used only to compare two passages against each
    other, so a consistent bias matters less than a correct absolute count.
    """
    cleaned = _NON_ALPHA.sub("", word.lower())
    if not cleaned:
        return 0
    count = 0
    previous_was_vowel = False
    for character in cleaned:
        is_vowel = character in _VOWELS
        if is_vowel and not previous_was_vowel:
            count += 1
        previous_was_vowel = is_vowel
    if cleaned.endswith("e") and count > 1:
        count -= 1
    return max(1, count)


def text_metrics(text):
    words = (text or "").split()
    sentences = [part for part in _SENTENCE_SPLIT.split(text or "") if part.strip()]
    word_count = len(words)
    sentence_count = len(sentences)
    safe_words = word_count or 1
    safe_sentences = sentence_count or 1
    syllables = sum(syllable_count(word) for word in words)
    fk = (
        0.39 * (safe_words / safe_sentences)
        + 11.8 * (syllables / safe_words)
        - 15.59
    )
    return {"words": word_count, "sentences": sentence_count, "fk": round(fk, 2)}


def compare(original_text, candidate_text):
    """Where does ``candidate_text`` sit relative to ``original_text``?

    Returns the proposed slot plus the margins behind it. ``confident`` is
    False whenever the two signals disagree on direction, regardless of how
    large either margin is: a passage can be longer and easier at the same
    time, and that is precisely when a guess is least defensible.
    """
    original = text_metrics(original_text)
    candidate = text_metrics(candidate_text)

    delta_fk = abs(candidate["fk"] - original["fk"])
    delta_words = abs(candidate["words"] - original["words"])
    smaller = min(candidate["words"], original["words"]) or 1
    ratio = max(candidate["words"], original["words"]) / smaller

    fk_says_elaborated = candidate["fk"] > original["fk"]
    words_say_elaborated = candidate["words"] > original["words"]
    agree = fk_says_elaborated == words_say_elaborated

    # Flesch-Kincaid leads the proposal; word count only breaks an FK tie.
    if candidate["fk"] == original["fk"]:
        slot = "ELABORATED" if words_say_elaborated else "SIMPLIFIED"
    else:
        slot = "ELABORATED" if fk_says_elaborated else "SIMPLIFIED"

    confident = (
        agree
        and delta_fk > FK_MARGIN
        and ratio >= WORD_RATIO
        and delta_words > WORD_DELTA
    )

    return {
        "slot": slot,
        "confident": confident,
        "agree": agree,
        "delta_fk": round(delta_fk, 2),
        "delta_words": delta_words,
        "ratio": round(ratio, 2),
    }
```

- [ ] **Step 4: Run the test to verify it passes**

Run from `backend/`:
```
..\.venv\Scripts\python.exe manage.py test course.test_readability --noinput
```
Expected: PASS, 12 tests.

- [ ] **Step 5: Verify against the six real pairs**

This is a sanity check on live data, not an automated test. Run from `backend/`:

```
..\.venv\Scripts\python.exe -c "import os,django;os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');django.setup();from collections import defaultdict;from lessons.models import LearningObject;from course.readability import compare;g=defaultdict(list);[g[o.group_id].append(o) for o in LearningObject.objects.exclude(group__isnull=True).select_related('material')];[print(gid, compare(sorted(v,key=lambda x:x.material_id)[0].content, sorted(v,key=lambda x:x.material_id)[1].content)) for gid,v in sorted(g.items()) if len(v)>1]"
```

Expected: six lines. Exactly two report `'confident': True` (groups 280 and 281). All six report `'agree': True`. If more than two are confident, the thresholds were transcribed wrong.

- [ ] **Step 6: Commit**

```bash
git add backend/course/readability.py backend/course/test_readability.py
git commit -m "Add relative readability comparison for learning object versions"
```

---

### Task 2: Schema for representation and variant provenance

**Files:**
- Modify: `backend/lessons/models.py:160-193` (add `represented_by` to `LearningObject`)
- Modify: `backend/course/models.py:64-99` (`LessonVariant`)
- Create: `backend/lessons/migrations/0015_learningobject_represented_by.py` (generated)
- Create: `backend/course/migrations/0005_lessonvariant_provenance.py` (generated)
- Test: `backend/course/test_version_schema.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `LearningObject.represented_by` — nullable self-FK, `related_name="represents"`
  - `LessonVariant.variant` accepts `"EXTRA"` in addition to `"SIMPLIFIED"` / `"ELABORATED"`
  - `LessonVariant.origin` — `"source_pdf"` or `"generated"`
  - `LessonVariant.source_learning_object` — nullable FK, `related_name="supplied_variants"`
  - `LessonVariant.assigned_by` — `"heuristic"` or `"teacher"`

**Note on `LessonVariant.clean()`:** it currently raises unless the object's material already has a `course_package_lesson_node`. That wrapper is created by `sync_course_outline()` at publish. Because this design moves assignment to the review step, which runs *before* publish, that check must go — otherwise every write in Task 3 fails validation. Removing it is safe: the wrapper is a packaging concern, and `LessonPackageService.build_package()` reads variants through the learning object, not through the wrapper.

- [ ] **Step 1: Write the failing test**

Create `backend/course/test_version_schema.py`:

```python
from django.db.utils import IntegrityError
from django.test import TestCase

from lessons.models import CourseGroup, LearningMaterial, LearningObject, OutlineNode

from .models import LessonVariant


class VersionSchemaTests(TestCase):
    def setUp(self):
        course = CourseGroup.objects.create(title="Grade 1 Science")
        node = OutlineNode.objects.create(course=course, title="Matter", order=0, depth=0)
        material = LearningMaterial.objects.create(course=course, outline_node=node, title="Lesson 1")
        self.original = LearningObject.objects.create(
            material=material, title="Solid", content="A solid holds its shape.", order=0
        )
        self.partner = LearningObject.objects.create(
            material=material, title="Solid again", content="A solid keeps a fixed shape.", order=1
        )

    def test_learning_object_can_be_represented_by_another(self):
        self.partner.represented_by = self.original
        self.partner.save()
        self.partner.refresh_from_db()
        self.assertEqual(self.partner.represented_by, self.original)
        self.assertIn(self.partner, self.original.represents.all())

    def test_representation_defaults_to_null(self):
        self.assertIsNone(self.original.represented_by)

    def test_variant_records_provenance(self):
        row = LessonVariant.objects.create(
            learning_object=self.original,
            variant="SIMPLIFIED",
            narration="A solid keeps a fixed shape.",
            origin="source_pdf",
            source_learning_object=self.partner,
            assigned_by="heuristic",
        )
        row.refresh_from_db()
        self.assertEqual(row.origin, "source_pdf")
        self.assertEqual(row.source_learning_object, self.partner)
        self.assertEqual(row.assigned_by, "heuristic")

    def test_primary_slots_are_single_occupancy(self):
        LessonVariant.objects.create(
            learning_object=self.original, variant="SIMPLIFIED", narration="One", origin="generated"
        )
        with self.assertRaises(IntegrityError):
            LessonVariant.objects.create(
                learning_object=self.original, variant="SIMPLIFIED", narration="Two", origin="generated"
            )

    def test_extras_are_not_constrained(self):
        LessonVariant.objects.create(
            learning_object=self.original, variant="EXTRA", narration="Third PDF wording", origin="source_pdf"
        )
        LessonVariant.objects.create(
            learning_object=self.original, variant="EXTRA", narration="Fourth PDF wording", origin="source_pdf"
        )
        self.assertEqual(self.original.variants.filter(variant="EXTRA").count(), 2)

    def test_variant_can_be_written_before_the_lesson_package_exists(self):
        # No CourseModule or LessonNode has been created for this material.
        LessonVariant.objects.create(
            learning_object=self.original, variant="ELABORATED", narration="Longer wording", origin="generated"
        )
        self.assertEqual(self.original.variants.count(), 1)
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `backend/`:
```
..\.venv\Scripts\python.exe manage.py test course.test_version_schema --noinput
```
Expected: FAIL — `TypeError: LearningObject() got unexpected keyword arguments: 'represented_by'` or a field error on `origin`.

- [ ] **Step 3: Add `represented_by` to `LearningObject`**

In `backend/lessons/models.py`, inside `class LearningObject`, directly after the `group` field:

```python
    # Set when semantic grouping decided another object teaches this same
    # concept. The row, its metadata_id and its question links are all kept:
    # grouping is an automatic decision at an unvalidated threshold, so it has
    # to stay reversible. Consumers skip represented objects when sequencing.
    represented_by = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        related_name="represents",
        on_delete=models.SET_NULL,
    )
```

- [ ] **Step 4: Update `LessonVariant`**

In `backend/course/models.py`, replace the `LessonVariant` class body's field block and `Meta`, and delete `clean()` and the `save()` override that calls it:

```python
class LessonVariant(models.Model):
    VARIANTS = [
        ("ELABORATED", "Elaborated"),
        ("SIMPLIFIED", "Simplified"),
        ("EXTRA", "Extra"),
    ]

    class Origin(models.TextChoices):
        SOURCE_PDF = "source_pdf", "Text from a grouped source PDF"
        GENERATED = "generated", "Generated by the adaptive variant model"

    class AssignedBy(models.TextChoices):
        HEURISTIC = "heuristic", "Proposed by the readability heuristic"
        TEACHER = "teacher", "Confirmed or corrected by a teacher"

    learning_object = models.ForeignKey(
        LearningObject,
        on_delete=models.CASCADE,
        related_name="variants",
    )
    variant = models.CharField(max_length=20, choices=VARIANTS)
    narration = models.TextField()
    audio_url = models.CharField(max_length=255, blank=True)
    source_fingerprint = models.CharField(max_length=64, blank=True)
    generator_model = models.CharField(max_length=100, blank=True)
    generated_at = models.DateTimeField(null=True, blank=True)
    origin = models.CharField(
        max_length=20,
        choices=Origin.choices,
        default=Origin.GENERATED,
    )
    # Which grouped object supplied this wording. Null for generated rows.
    source_learning_object = models.ForeignKey(
        LearningObject,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="supplied_variants",
    )
    assigned_by = models.CharField(
        max_length=20,
        choices=AssignedBy.choices,
        default=AssignedBy.HEURISTIC,
    )

    class Meta:
        ordering = ["learning_object_id", "variant"]
        constraints = [
            # An object has at most one simplified and one elaborated version.
            # Extras are unconstrained: a fourth PDF can supply several.
            models.UniqueConstraint(
                fields=["learning_object", "variant"],
                condition=~models.Q(variant="EXTRA"),
                name="unique_primary_variant_slot",
            ),
        ]

    @property
    def lesson_node(self):
        return self.learning_object.material.course_package_lesson_node

    def __str__(self):
        return f"[{self.variant}] {self.learning_object.title}"
```

Remove the now-unused `ValidationError` import at the top of the file if nothing else uses it. Check first:

```bash
grep -n "ValidationError" backend/course/models.py
```

- [ ] **Step 5: Generate and inspect the migrations**

Run from `backend/`:
```
..\.venv\Scripts\python.exe manage.py makemigrations lessons course
```

Open both generated files and confirm: the `lessons` migration only adds `represented_by`; the `course` migration adds three fields, alters `variant`'s choices, removes `unique_together`, and adds `unique_primary_variant_slot`. If `unique_together` removal is missing, the constraint will conflict at runtime.

- [ ] **Step 6: Apply and run the test**

Run from `backend/`:
```
..\.venv\Scripts\python.exe manage.py migrate
..\.venv\Scripts\python.exe manage.py test course.test_version_schema --noinput
```
Expected: PASS, 6 tests.

- [ ] **Step 7: Confirm nothing regressed**

Run from `backend/`:
```
..\.venv\Scripts\python.exe manage.py test lessons course --noinput
```
Expected: OK. The count will exceed the previous 187 by the tests added so far.

- [ ] **Step 8: Commit**

```bash
git add backend/lessons/models.py backend/course/models.py backend/lessons/migrations backend/course/migrations backend/course/test_version_schema.py
git commit -m "Add representation flag and variant provenance"
```

---

### Task 3: Assign versions from real grouped text

No LLM in this task. It establishes representative selection, slot assignment, and collision handling using only text the teachers wrote.

**Files:**
- Create: `backend/course/version_assignment.py`
- Test: `backend/course/test_version_assignment.py`

**Interfaces:**
- Consumes: `course.readability.compare`; `LessonVariant.origin` / `source_learning_object` / `assigned_by` from Task 2.
- Produces:
  - `choose_representative(members: list) -> LearningObject`
  - `assign_group_versions(group) -> dict` with keys `representative_id: int`, `assigned: list[dict]`, `needs_confirmation: list[dict]`, `extras: int`

`assign_group_versions` writes `LessonVariant` rows for the representative and returns a summary. Each entry of `assigned` and `needs_confirmation` is `{"learning_object_id": int, "slot": str, "confident": bool, "delta_fk": float, "delta_words": int, "ratio": float}`.

- [ ] **Step 1: Write the failing test**

Create `backend/course/test_version_assignment.py`:

```python
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from lessons.models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
)

from .models import LessonVariant
from .version_assignment import assign_group_versions, choose_representative


SHORT = "Solid has a fixed shape. It holds its form. It does not flow."
LONG = (
    "A solid is a state of matter that maintains a fixed shape and a fixed volume. "
    "The particles inside it are packed tightly together in a regular arrangement. "
    "Because those particles cannot move past one another, a solid does not flow."
)
MIDDLING = "A solid keeps its shape. The particles are packed closely. It will not flow away."


class VersionAssignmentTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.node = OutlineNode.objects.create(
            course=self.course, title="Matter", order=0, depth=0
        )
        self.group = LearningObjectGroup.objects.create(outline_node=self.node)
        self.now = timezone.now()

    def _material(self, title, minutes_offset):
        material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.node, title=title
        )
        LearningMaterial.objects.filter(pk=material.pk).update(
            created_at=self.now + timedelta(minutes=minutes_offset)
        )
        material.refresh_from_db()
        return material

    def _object(self, material, content, title="Solid"):
        return LearningObject.objects.create(
            material=material, group=self.group, title=title, content=content, order=0
        )

    def test_representative_is_the_earliest_uploaded_member(self):
        first = self._object(self._material("PDF one", 0), SHORT)
        second = self._object(self._material("PDF two", 5), LONG)
        self.assertEqual(choose_representative([second, first]), first)

    def test_confident_partner_is_assigned_and_stored_with_provenance(self):
        first = self._object(self._material("PDF one", 0), SHORT)
        second = self._object(self._material("PDF two", 5), LONG)

        result = assign_group_versions(self.group)

        self.assertEqual(result["representative_id"], first.id)
        self.assertEqual(len(result["assigned"]), 1)
        self.assertEqual(result["needs_confirmation"], [])

        row = LessonVariant.objects.get(learning_object=first)
        self.assertEqual(row.variant, "ELABORATED")
        self.assertEqual(row.narration, LONG)
        self.assertEqual(row.origin, "source_pdf")
        self.assertEqual(row.source_learning_object, second)

    def test_thin_margin_is_routed_to_the_teacher_not_stored(self):
        first = self._object(self._material("PDF one", 0), SHORT)
        self._object(self._material("PDF two", 5), MIDDLING)

        result = assign_group_versions(self.group)

        self.assertEqual(result["assigned"], [])
        self.assertEqual(len(result["needs_confirmation"]), 1)
        self.assertFalse(result["needs_confirmation"][0]["confident"])
        self.assertFalse(LessonVariant.objects.filter(learning_object=first).exists())

    def test_slot_collision_keeps_the_larger_margin_and_stores_an_extra(self):
        first = self._object(self._material("PDF one", 0), SHORT)
        bigger = self._object(self._material("PDF two", 5), LONG)
        smaller = self._object(
            self._material("PDF three", 10),
            "A solid is a state of matter that keeps a fixed shape and a fixed volume overall.",
        )

        result = assign_group_versions(self.group)

        elaborated = LessonVariant.objects.get(learning_object=first, variant="ELABORATED")
        self.assertEqual(elaborated.source_learning_object, bigger)
        extra = LessonVariant.objects.get(learning_object=first, variant="EXTRA")
        self.assertEqual(extra.source_learning_object, smaller)
        self.assertEqual(result["extras"], 1)

    def test_singleton_group_assigns_nothing(self):
        self._object(self._material("PDF one", 0), SHORT)
        result = assign_group_versions(self.group)
        self.assertEqual(result["assigned"], [])
        self.assertEqual(result["needs_confirmation"], [])

    def test_reassignment_is_idempotent(self):
        self._object(self._material("PDF one", 0), SHORT)
        self._object(self._material("PDF two", 5), LONG)

        assign_group_versions(self.group)
        assign_group_versions(self.group)

        self.assertEqual(LessonVariant.objects.filter(variant="ELABORATED").count(), 1)
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `backend/`:
```
..\.venv\Scripts\python.exe manage.py test course.test_version_assignment --noinput
```
Expected: FAIL — `ModuleNotFoundError: No module named 'course.version_assignment'`

- [ ] **Step 3: Write the implementation**

Create `backend/course/version_assignment.py`:

```python
"""Decide which grouped text becomes which version of a learning object.

A group's members are alternative presentations of one concept, written by
different teachers. The earliest upload is the original; the rest are ranked
against it. Nothing here calls a language model -- this task only distributes
text that already exists.
"""

import logging

from .models import LessonVariant
from .readability import compare


logger = logging.getLogger(__name__)

PRIMARY_SLOTS = ("SIMPLIFIED", "ELABORATED")


def choose_representative(members):
    """The earliest-uploaded member. Ties break on material id, then object id.

    Deterministic ordering matters: the representative decides which object
    survives as a teaching step, and a reshuffle on every refresh would churn
    question links and the prerequisite graph.
    """
    return sorted(
        members,
        key=lambda item: (item.material.created_at, item.material_id, item.id),
    )[0]


def assign_group_versions(group):
    """Fill this group's primary slots from its own members.

    Confident comparisons are written immediately. Unconfident ones are
    returned for a teacher to settle and are deliberately NOT stored: an
    unreviewed guess in the database is indistinguishable from a decision.
    """
    members = [
        item
        for item in group.learning_objects.select_related("material").all()
        if (item.content or "").strip()
    ]
    if len(members) < 2:
        return {
            "representative_id": members[0].id if members else None,
            "assigned": [],
            "needs_confirmation": [],
            "extras": 0,
        }

    representative = choose_representative(members)
    candidates = [item for item in members if item.id != representative.id]

    proposals = []
    for candidate in candidates:
        verdict = compare(representative.content, candidate.content)
        proposals.append({
            "learning_object_id": candidate.id,
            "object": candidate,
            "slot": verdict["slot"],
            "confident": verdict["confident"],
            "delta_fk": verdict["delta_fk"],
            "delta_words": verdict["delta_words"],
            "ratio": verdict["ratio"],
        })

    assigned = []
    needs_confirmation = []
    extras = 0
    claimed = {}

    # Strongest margin first, so a collision resolves in favour of the clearer
    # of the two claims rather than whichever happened to be ordered first.
    for proposal in sorted(proposals, key=lambda item: -item["delta_fk"]):
        if not proposal["confident"]:
            needs_confirmation.append(_public(proposal))
            continue
        slot = proposal["slot"]
        if slot in claimed:
            _store(representative, proposal["object"], "EXTRA")
            extras += 1
            continue
        claimed[slot] = proposal["object"]
        _store(representative, proposal["object"], slot)
        assigned.append(_public(proposal))

    return {
        "representative_id": representative.id,
        "assigned": assigned,
        "needs_confirmation": needs_confirmation,
        "extras": extras,
    }


def _public(proposal):
    return {key: value for key, value in proposal.items() if key != "object"}


def _store(representative, source, slot):
    if slot == "EXTRA":
        LessonVariant.objects.update_or_create(
            learning_object=representative,
            variant="EXTRA",
            source_learning_object=source,
            defaults={
                "narration": source.content,
                "origin": LessonVariant.Origin.SOURCE_PDF,
                "assigned_by": LessonVariant.AssignedBy.HEURISTIC,
            },
        )
        return
    LessonVariant.objects.update_or_create(
        learning_object=representative,
        variant=slot,
        defaults={
            "narration": source.content,
            "origin": LessonVariant.Origin.SOURCE_PDF,
            "source_learning_object": source,
            "assigned_by": LessonVariant.AssignedBy.HEURISTIC,
        },
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run from `backend/`:
```
..\.venv\Scripts\python.exe manage.py test course.test_version_assignment --noinput
```
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add backend/course/version_assignment.py backend/course/test_version_assignment.py
git commit -m "Assign learning object versions from grouped source text"
```

---

### Task 4: Fill remaining slots with the LLM

**Files:**
- Modify: `backend/course/variant_generator.py` (add `fill_missing_slots`, keep `generate_standalone_variants` working)
- Test: `backend/course/test_gap_filling.py`

**Interfaces:**
- Consumes: `LessonVariant` from Task 2; `_request_variants(learning_object, model)` which already exists at `backend/course/variant_generator.py:92` and returns `{"SIMPLIFIED": str, "ELABORATED": str}`.
- Produces: `fill_missing_slots(learning_object) -> dict` with keys `generated: list[str]`, `skipped: list[str]`, `errors: list[dict]`.

`_request_variants` returns both slots in one call. When only one slot is missing, the other returned value is discarded — one Gemma call either way, and reusing the existing grounded prompt keeps the word-count limits and the "use only facts in SOURCE" constraint intact.

- [ ] **Step 1: Write the failing test**

Create `backend/course/test_gap_filling.py`:

```python
from unittest.mock import patch

from django.test import TestCase

from lessons.models import CourseGroup, LearningMaterial, LearningObject, OutlineNode

from .models import LessonVariant
from .variant_generator import VariantGenerationError, fill_missing_slots


class GapFillingTests(TestCase):
    def setUp(self):
        course = CourseGroup.objects.create(title="Grade 1 Science")
        node = OutlineNode.objects.create(course=course, title="Matter", order=0, depth=0)
        material = LearningMaterial.objects.create(course=course, outline_node=node, title="Lesson 1")
        self.obj = LearningObject.objects.create(
            material=material, title="Solid", content="A solid holds its shape and does not flow.", order=0
        )

    @patch("course.variant_generator._request_variants")
    def test_fills_both_slots_when_none_exist(self, request_variants):
        request_variants.return_value = {"SIMPLIFIED": "Solid keeps shape.", "ELABORATED": "A solid holds a fixed shape."}

        result = fill_missing_slots(self.obj)

        self.assertCountEqual(result["generated"], ["SIMPLIFIED", "ELABORATED"])
        self.assertEqual(self.obj.variants.count(), 2)
        self.assertTrue(all(row.origin == "generated" for row in self.obj.variants.all()))

    @patch("course.variant_generator._request_variants")
    def test_only_fills_the_missing_slot(self, request_variants):
        LessonVariant.objects.create(
            learning_object=self.obj, variant="ELABORATED", narration="Real teacher text", origin="source_pdf"
        )
        request_variants.return_value = {"SIMPLIFIED": "Solid keeps shape.", "ELABORATED": "ignored"}

        result = fill_missing_slots(self.obj)

        self.assertEqual(result["generated"], ["SIMPLIFIED"])
        self.assertEqual(result["skipped"], ["ELABORATED"])
        self.assertEqual(
            LessonVariant.objects.get(learning_object=self.obj, variant="ELABORATED").narration,
            "Real teacher text",
        )

    @patch("course.variant_generator._request_variants")
    def test_never_overwrites_source_pdf_text(self, request_variants):
        LessonVariant.objects.create(
            learning_object=self.obj, variant="SIMPLIFIED", narration="Human wording", origin="source_pdf"
        )
        LessonVariant.objects.create(
            learning_object=self.obj, variant="ELABORATED", narration="Human wording two", origin="source_pdf"
        )

        result = fill_missing_slots(self.obj)

        request_variants.assert_not_called()
        self.assertEqual(result["generated"], [])

    @patch("course.variant_generator._request_variants")
    def test_failure_leaves_the_slot_empty_and_is_reported(self, request_variants):
        request_variants.side_effect = VariantGenerationError("Gemma did not return valid JSON.")

        result = fill_missing_slots(self.obj)

        self.assertEqual(result["generated"], [])
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("valid JSON", result["errors"][0]["detail"])
        self.assertEqual(self.obj.variants.count(), 0)

    def test_empty_content_generates_nothing(self):
        self.obj.content = "   "
        self.obj.save()
        result = fill_missing_slots(self.obj)
        self.assertEqual(result["generated"], [])
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `backend/`:
```
..\.venv\Scripts\python.exe manage.py test course.test_gap_filling --noinput
```
Expected: FAIL — `ImportError: cannot import name 'fill_missing_slots'`

- [ ] **Step 3: Write the implementation**

Append to `backend/course/variant_generator.py`:

```python
def fill_missing_slots(learning_object):
    """Generate only the primary slots real source text did not supply.

    Rows whose origin is ``source_pdf`` are never touched: a teacher wrote
    that text and it cannot be regenerated. A generation failure leaves the
    slot empty and is reported, so the review screen can show it as incomplete
    rather than the pipeline silently publishing two versions as three.
    """
    if not settings.ADAPTIVE_VARIANT_GENERATION_ENABLED:
        return {"generated": [], "skipped": [], "errors": []}
    if not (learning_object.content or "").strip():
        return {"generated": [], "skipped": [], "errors": []}

    existing = set(
        learning_object.variants.filter(
            variant__in=("SIMPLIFIED", "ELABORATED"),
        ).values_list("variant", flat=True)
    )
    missing = [slot for slot in ("SIMPLIFIED", "ELABORATED") if slot not in existing]
    if not missing:
        return {"generated": [], "skipped": sorted(existing), "errors": []}

    model = settings.ADAPTIVE_VARIANT_LLM_MODEL
    fingerprint = _fingerprint(learning_object)
    try:
        variants = _request_variants(learning_object, model)
    except VariantGenerationError as exc:
        logger.warning(
            "Adaptive variant generation failed: learning_object=%s model=%s error=%s",
            learning_object.id,
            model,
            exc,
        )
        return {
            "generated": [],
            "skipped": sorted(existing),
            "errors": [{"learning_object_id": learning_object.id, "detail": str(exc)}],
        }

    generated = []
    with transaction.atomic():
        for slot in missing:
            LessonVariant.objects.update_or_create(
                learning_object=learning_object,
                variant=slot,
                defaults={
                    "narration": variants[slot],
                    "audio_url": "",
                    "source_fingerprint": fingerprint,
                    "generator_model": model,
                    "generated_at": timezone.now(),
                    "origin": LessonVariant.Origin.GENERATED,
                    "source_learning_object": None,
                },
            )
            generated.append(slot)

    return {"generated": generated, "skipped": sorted(existing), "errors": []}
```

- [ ] **Step 4: Run the test to verify it passes**

Run from `backend/`:
```
..\.venv\Scripts\python.exe manage.py test course.test_gap_filling --noinput
```
Expected: PASS, 5 tests.

- [ ] **Step 5: Confirm the existing generator still passes**

Run from `backend/`:
```
..\.venv\Scripts\python.exe manage.py test course --noinput
```
Expected: OK. `course/tests.py` covers `generate_standalone_variants`, which is untouched.

- [ ] **Step 6: Commit**

```bash
git add backend/course/variant_generator.py backend/course/test_gap_filling.py
git commit -m "Generate only the version slots source text did not supply"
```

---

### Task 5: Apply and release representation

**Files:**
- Modify: `backend/course/version_assignment.py`
- Test: `backend/course/test_representation.py`

**Interfaces:**
- Consumes: `assign_group_versions` (Task 3), `fill_missing_slots` (Task 4), `LearningObject.represented_by` (Task 2).
- Produces:
  - `settle_group(group) -> dict` — assign, fill gaps, then flag. Keys: `representative_id`, `assigned`, `needs_confirmation`, `extras`, `generated`, `errors`.
  - `release_learning_object(learning_object) -> None` — undo representation for one object.

- [ ] **Step 1: Write the failing test**

Create `backend/course/test_representation.py`:

```python
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from lessons.models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
)

from .models import LessonVariant
from .version_assignment import release_learning_object, settle_group


SHORT = "Solid has a fixed shape. It holds its form. It does not flow."
LONG = (
    "A solid is a state of matter that maintains a fixed shape and a fixed volume. "
    "The particles inside it are packed tightly together in a regular arrangement. "
    "Because those particles cannot move past one another, a solid does not flow."
)


class RepresentationTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.node = OutlineNode.objects.create(
            course=self.course, title="Matter", order=0, depth=0
        )
        self.group = LearningObjectGroup.objects.create(outline_node=self.node)
        now = timezone.now()
        self.first = self._object("PDF one", now, SHORT)
        self.second = self._object("PDF two", now + timedelta(minutes=5), LONG)

    def _object(self, title, created_at, content):
        material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.node, title=title
        )
        LearningMaterial.objects.filter(pk=material.pk).update(created_at=created_at)
        material.refresh_from_db()
        return LearningObject.objects.create(
            material=material, group=self.group, title="Solid", content=content, order=0
        )

    @patch("course.variant_generator._request_variants")
    def test_partner_is_flagged_and_representative_is_not(self, request_variants):
        request_variants.return_value = {"SIMPLIFIED": "Solid keeps shape.", "ELABORATED": "x"}

        settle_group(self.group)

        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertIsNone(self.first.represented_by)
        self.assertEqual(self.second.represented_by, self.first)

    @patch("course.variant_generator._request_variants")
    def test_gap_is_filled_after_real_text_is_placed(self, request_variants):
        request_variants.return_value = {"SIMPLIFIED": "Solid keeps shape.", "ELABORATED": "ignored"}

        result = settle_group(self.group)

        self.assertEqual(result["generated"], ["SIMPLIFIED"])
        elaborated = LessonVariant.objects.get(learning_object=self.first, variant="ELABORATED")
        self.assertEqual(elaborated.origin, "source_pdf")
        simplified = LessonVariant.objects.get(learning_object=self.first, variant="SIMPLIFIED")
        self.assertEqual(simplified.origin, "generated")

    @patch("course.variant_generator._request_variants")
    def test_release_clears_the_flag_and_drops_only_generated_rows(self, request_variants):
        request_variants.return_value = {"SIMPLIFIED": "Solid keeps shape.", "ELABORATED": "ignored"}
        settle_group(self.group)

        release_learning_object(self.second)

        self.second.refresh_from_db()
        self.assertIsNone(self.second.represented_by)
        self.assertFalse(
            LessonVariant.objects.filter(learning_object=self.first, origin="generated").exists()
        )
        self.assertTrue(
            LessonVariant.objects.filter(learning_object=self.first, origin="source_pdf").exists()
        )

    @patch("course.variant_generator._request_variants")
    def test_unconfident_group_flags_nobody(self, request_variants):
        request_variants.return_value = {"SIMPLIFIED": "a", "ELABORATED": "b"}
        middling = self._object(
            "PDF three", timezone.now() + timedelta(minutes=9),
            "A solid keeps its shape. The particles are packed closely. It will not flow away.",
        )
        LearningObject.objects.filter(pk=self.second.pk).update(group=None)

        settle_group(self.group)

        middling.refresh_from_db()
        self.assertIsNone(middling.represented_by)
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `backend/`:
```
..\.venv\Scripts\python.exe manage.py test course.test_representation --noinput
```
Expected: FAIL — `ImportError: cannot import name 'settle_group'`

- [ ] **Step 3: Write the implementation**

Append to `backend/course/version_assignment.py`:

```python
def settle_group(group):
    """Distribute real text, fill what is missing, then collapse the group.

    Flagging happens last and only for members whose text was actually placed.
    A member awaiting teacher confirmation stays a teaching step: until someone
    decides where its text belongs, removing it from the lesson would drop
    content rather than deduplicate it.
    """
    from .variant_generator import fill_missing_slots

    outcome = assign_group_versions(group)
    if outcome["representative_id"] is None:
        return {**outcome, "generated": [], "errors": []}

    members = {item.id: item for item in group.learning_objects.select_related("material")}
    representative = members[outcome["representative_id"]]

    filled = fill_missing_slots(representative)

    placed_ids = {entry["learning_object_id"] for entry in outcome["assigned"]}
    placed_ids |= set(
        LessonVariant.objects.filter(
            learning_object=representative,
            variant="EXTRA",
        ).values_list("source_learning_object_id", flat=True)
    )
    for member_id in placed_ids:
        member = members.get(member_id)
        if member is not None and member.represented_by_id != representative.id:
            member.represented_by = representative
            member.save(update_fields=["represented_by"])

    return {
        **outcome,
        "generated": filled["generated"],
        "errors": filled["errors"],
    }


def release_learning_object(learning_object):
    """Undo representation for one object after a teacher ungroups it.

    Generated rows on the representative are dropped because the object that
    justified them is leaving. Source rows are never dropped -- a teacher wrote
    that text, and regenerating it is not possible.
    """
    representative = learning_object.represented_by
    if representative is None:
        return

    LessonVariant.objects.filter(
        learning_object=representative,
        source_learning_object=learning_object,
    ).delete()
    LessonVariant.objects.filter(
        learning_object=representative,
        origin=LessonVariant.Origin.GENERATED,
    ).delete()

    learning_object.represented_by = None
    learning_object.save(update_fields=["represented_by"])
```

- [ ] **Step 4: Run the test to verify it passes**

Run from `backend/`:
```
..\.venv\Scripts\python.exe manage.py test course.test_representation --noinput
```
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add backend/course/version_assignment.py backend/course/test_representation.py
git commit -m "Collapse settled groups and support releasing them"
```

---

### Task 6: Exclude represented objects from the lesson package

**Files:**
- Modify: `backend/course/services.py:240-258` (`_build_chunk`), `backend/course/services.py:286` (`build_package`)
- Test: `backend/course/test_package_versions.py`

**Interfaces:**
- Consumes: `LearningObject.represented_by` (Task 2), `LessonVariant.origin` (Task 2).
- Produces: chunk dicts gain `"versions_complete": bool` and each variant entry gains `"origin": str`.

- [ ] **Step 1: Write the failing test**

Create `backend/course/test_package_versions.py`:

```python
from django.test import TestCase

from lessons.models import CourseGroup, LearningMaterial, LearningObject, OutlineNode

from .models import CourseModule, LessonNode, LessonVariant
from .services import LessonPackageService


class PackageVersionTests(TestCase):
    def setUp(self):
        course = CourseGroup.objects.create(title="Grade 1 Science")
        root = OutlineNode.objects.create(course=course, title="Matter", order=0, depth=0)
        self.material = LearningMaterial.objects.create(
            course=course, outline_node=root, title="Lesson 1"
        )
        module = CourseModule.objects.create(source=root)
        self.node = LessonNode.objects.create(module=module, source=self.material)
        self.original = LearningObject.objects.create(
            material=self.material, title="Solid", content="A solid holds its shape.", order=0
        )
        self.partner = LearningObject.objects.create(
            material=self.material, title="Solid again", content="A solid keeps a fixed shape.", order=1
        )

    def test_represented_objects_are_not_chunks(self):
        self.partner.represented_by = self.original
        self.partner.save()

        package = LessonPackageService.build_package(self.node.id)

        ids = [chunk["id"] for chunk in package["chunks"]]
        self.assertIn(self.original.id, ids)
        self.assertNotIn(self.partner.id, ids)

    def test_chunk_reports_variant_origin(self):
        LessonVariant.objects.create(
            learning_object=self.original,
            variant="SIMPLIFIED",
            narration="Solid keeps shape.",
            origin="source_pdf",
            source_learning_object=self.partner,
        )

        package = LessonPackageService.build_package(self.node.id)
        chunk = next(c for c in package["chunks"] if c["id"] == self.original.id)

        self.assertEqual(chunk["variants"]["simplified"]["origin"], "source_pdf")
        self.assertEqual(chunk["variants"]["normal"]["origin"], "original")

    def test_chunk_flags_incomplete_versions(self):
        package = LessonPackageService.build_package(self.node.id)
        chunk = next(c for c in package["chunks"] if c["id"] == self.original.id)
        self.assertFalse(chunk["versions_complete"])

    def test_chunk_is_complete_with_both_slots(self):
        for slot in ("SIMPLIFIED", "ELABORATED"):
            LessonVariant.objects.create(
                learning_object=self.original, variant=slot, narration=f"{slot} text", origin="generated"
            )

        package = LessonPackageService.build_package(self.node.id)
        chunk = next(c for c in package["chunks"] if c["id"] == self.original.id)

        self.assertTrue(chunk["versions_complete"])

    def test_extras_are_not_offered_as_a_version(self):
        LessonVariant.objects.create(
            learning_object=self.original, variant="EXTRA", narration="Third wording", origin="source_pdf"
        )
        package = LessonPackageService.build_package(self.node.id)
        chunk = next(c for c in package["chunks"] if c["id"] == self.original.id)
        self.assertNotIn("extra", chunk["variants"])
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `backend/`:
```
..\.venv\Scripts\python.exe manage.py test course.test_package_versions --noinput
```
Expected: FAIL — the partner appears in chunk ids, and `KeyError: 'origin'`.

- [ ] **Step 3: Update `_build_chunk`**

In `backend/course/services.py`, replace `_build_chunk` (currently lines 240-258):

```python
def _build_chunk(learning_object):
    variants = {}

    normal = normal_variant_for(learning_object)
    if normal:
        variants["normal"] = {
            "text": normal["narration"],
            "audio_url": normal["audio_url"],
            "origin": "original",
        }
    else:
        variants["normal"] = {
            "text": learning_object.content,
            "audio_url": "",
            "origin": "original",
        }

    for row in learning_object.variants.all():
        # Extras are retained for the interaction pipeline to rule on later.
        # They are not one of the three versions a student is offered.
        if row.variant == "EXTRA":
            continue
        variants[row.variant.lower()] = {
            "text": row.narration,
            "audio_url": row.audio_url,
            "origin": row.origin,
        }

    return {
        "id": learning_object.id,
        "metadata_id": str(learning_object.metadata_id),
        "order": learning_object.order,
        "title": learning_object.title,
        "variants": variants,
        "versions_complete": {"simplified", "elaborated"}.issubset(variants.keys()),
    }
```

- [ ] **Step 4: Filter represented objects out of the package**

In `backend/course/services.py`, inside `LessonPackageService.build_package`, replace the `learning_objects` line (currently line 286):

```python
        learning_objects = list(
            node.learning_objects.filter(represented_by__isnull=True).order_by("order", "id")
        )
```

- [ ] **Step 5: Run the test to verify it passes**

Run from `backend/`:
```
..\.venv\Scripts\python.exe manage.py test course.test_package_versions --noinput
```
Expected: PASS, 5 tests.

- [ ] **Step 6: Confirm nothing regressed**

Run from `backend/`:
```
..\.venv\Scripts\python.exe manage.py test lessons course --noinput
```
Expected: OK.

- [ ] **Step 7: Commit**

```bash
git add backend/course/services.py backend/course/test_package_versions.py
git commit -m "Serve one chunk per concept with version provenance"
```

---

### Task 7: Review payload and teacher confirmation

**Files:**
- Modify: `backend/lessons/views.py:129-200` (add versions to `_learning_resources_payload`)
- Modify: `backend/lessons/views.py` (new action `confirm_version_assignment`)
- Test: `backend/lessons/test_version_review.py`

**Interfaces:**
- Consumes: `settle_group` (Task 5), `LessonVariant` (Task 2).
- Produces:
  - Each group entry in the learning-resources payload gains `"versions": {"representative_id": int|None, "slots": dict, "needs_confirmation": list, "complete": bool}`.
  - `POST /api/courses/<pk>/outline-nodes/<node_id>/version-assignment/` with body `{"learning_object_id": int, "slot": "SIMPLIFIED"|"ELABORATED"|"EXTRA"}` — records a teacher decision and returns the refreshed learning-resources payload.

- [ ] **Step 1: Write the failing test**

Create `backend/lessons/test_version_review.py`:

```python
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from course.models import LessonVariant

from .models import CourseGroup, LearningMaterial, LearningObject, LearningObjectGroup, OutlineNode


SHORT = "Solid has a fixed shape. It holds its form. It does not flow."
MIDDLING = "A solid keeps its shape. The particles are packed closely. It will not flow away."


class VersionReviewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="teacher", password="pw", role="TEACHER"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.node = OutlineNode.objects.create(
            course=self.course, title="Matter", order=0, depth=0
        )
        self.group = LearningObjectGroup.objects.create(outline_node=self.node)
        now = timezone.now()
        self.first = self._object("PDF one", now, SHORT)
        self.second = self._object("PDF two", now + timedelta(minutes=5), MIDDLING)

    def _object(self, title, created_at, content):
        material = LearningMaterial.objects.create(
            course=self.course,
            outline_node=self.node,
            title=title,
            generated_json={"learning_objects_confirmed": True},
        )
        LearningMaterial.objects.filter(pk=material.pk).update(created_at=created_at)
        material.refresh_from_db()
        return LearningObject.objects.create(
            material=material, group=self.group, title="Solid", content=content, order=0
        )

    def _resources(self):
        return self.client.get(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/learning-resources/"
        )

    def test_payload_reports_a_pair_awaiting_confirmation(self):
        response = self._resources()
        self.assertEqual(response.status_code, 200)
        group = response.data["learning_object_groups"][0]
        self.assertEqual(group["versions"]["representative_id"], self.first.id)
        self.assertEqual(len(group["versions"]["needs_confirmation"]), 1)
        self.assertFalse(group["versions"]["complete"])

    def test_teacher_can_assign_a_slot(self):
        response = self.client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/version-assignment/",
            {"learning_object_id": self.second.id, "slot": "SIMPLIFIED"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        row = LessonVariant.objects.get(learning_object=self.first, variant="SIMPLIFIED")
        self.assertEqual(row.narration, MIDDLING)
        self.assertEqual(row.origin, "source_pdf")
        self.assertEqual(row.assigned_by, "teacher")

    def test_assignment_flags_the_assigned_object(self):
        self.client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/version-assignment/",
            {"learning_object_id": self.second.id, "slot": "SIMPLIFIED"},
            format="json",
        )
        self.second.refresh_from_db()
        self.assertEqual(self.second.represented_by, self.first)

    def test_invalid_slot_is_rejected(self):
        response = self.client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/version-assignment/",
            {"learning_object_id": self.second.id, "slot": "NORMAL"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_object_outside_the_topic_is_rejected(self):
        other_course = CourseGroup.objects.create(title="Other")
        other_node = OutlineNode.objects.create(
            course=other_course, title="Elsewhere", order=0, depth=0
        )
        other_material = LearningMaterial.objects.create(
            course=other_course, outline_node=other_node, title="X"
        )
        stranger = LearningObject.objects.create(
            material=other_material, title="X", content="Y", order=0
        )

        response = self.client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/version-assignment/",
            {"learning_object_id": stranger.id, "slot": "SIMPLIFIED"},
            format="json",
        )
        self.assertEqual(response.status_code, 404)
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `backend/`:
```
..\.venv\Scripts\python.exe manage.py test lessons.test_version_review --noinput
```
Expected: FAIL — `KeyError: 'versions'` and 404 on the new route.

- [ ] **Step 3: Add versions to the review payload**

In `backend/lessons/views.py`, add the import near the other `course` imports at the top:

```python
from course.version_assignment import assign_group_versions, settle_group
```

Then in `_learning_resources_payload`, inside the `for group in group_queryset:` loop, extend the dict appended to `groups` with a `versions` key. Add this immediately before `groups.append(`:

```python
            version_state = assign_group_versions(group)
            slot_rows = {}
            if version_state["representative_id"] is not None:
                slot_rows = {
                    row.variant.lower(): {
                        "text": row.narration,
                        "origin": row.origin,
                        "assigned_by": row.assigned_by,
                        "source_learning_object_id": row.source_learning_object_id,
                    }
                    for row in LessonVariant.objects.filter(
                        learning_object_id=version_state["representative_id"],
                    ).exclude(variant="EXTRA")
                }
```

and add to the appended dict:

```python
                    "versions": {
                        "representative_id": version_state["representative_id"],
                        "slots": slot_rows,
                        "needs_confirmation": version_state["needs_confirmation"],
                        "complete": {"simplified", "elaborated"}.issubset(slot_rows.keys()),
                    },
```

Add the model import at the top of the file:

```python
from course.models import LessonVariant
```

- [ ] **Step 4: Add the confirmation endpoint**

In `backend/lessons/views.py`, add this action to `CourseGroupViewSet`, directly after `publish_topic`:

```python
    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/version-assignment",
    )
    def confirm_version_assignment(self, request, pk=None, node_id=None):
        """Record a teacher's ruling on which version slot a grouped text fills."""
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response({"detail": "Outline node not found."}, status=status.HTTP_404_NOT_FOUND)

        slot = str(request.data.get("slot", "")).upper()
        if slot not in ("SIMPLIFIED", "ELABORATED", "EXTRA"):
            return Response(
                {"detail": "slot must be SIMPLIFIED, ELABORATED or EXTRA."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            learning_object = LearningObject.objects.select_related("group", "material").get(
                pk=request.data.get("learning_object_id"),
                material__outline_node=node,
            )
        except (LearningObject.DoesNotExist, TypeError, ValueError):
            return Response(
                {"detail": "Learning object not found in this topic."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if learning_object.group_id is None:
            return Response(
                {"detail": "Only grouped learning objects have version slots."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        state = assign_group_versions(learning_object.group)
        representative_id = state["representative_id"]
        if representative_id is None or representative_id == learning_object.id:
            return Response(
                {"detail": "This object is the original and cannot fill another slot."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        LessonVariant.objects.update_or_create(
            learning_object_id=representative_id,
            variant=slot,
            defaults={
                "narration": learning_object.content,
                "origin": LessonVariant.Origin.SOURCE_PDF,
                "source_learning_object": learning_object,
                "assigned_by": LessonVariant.AssignedBy.TEACHER,
            },
        )
        LearningObject.objects.filter(pk=learning_object.pk).update(
            represented_by_id=representative_id
        )

        return Response(self._learning_resources_payload(node, request))
```

`_learning_resources_payload` returns a plain dict; every neighbouring action
wraps it in `Response(...)`. See `backend/lessons/views.py:266` for the pattern.

- [ ] **Step 5: Run the test to verify it passes**

Run from `backend/`:
```
..\.venv\Scripts\python.exe manage.py test lessons.test_version_review --noinput
```
Expected: PASS, 5 tests.

- [ ] **Step 6: Commit**

```bash
git add backend/lessons/views.py backend/lessons/test_version_review.py
git commit -m "Expose version assignment for teacher review"
```

---

### Task 8: Make publish a backstop

**Files:**
- Modify: `backend/lessons/views.py:862-900` (`publish_topic`)
- Test: `backend/lessons/test_publish_versions.py`

**Interfaces:**
- Consumes: `settle_group` (Task 5).
- Produces: `publish["adaptive_variants_generated"]`, `publish["adaptive_variant_errors"]`, and a new `publish["incomplete_versions"]` — learning object ids still missing a slot.

- [ ] **Step 1: Write the failing test**

Create `backend/lessons/test_publish_versions.py`:

```python
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from course.models import LessonVariant

from .models import CourseGroup, LearningMaterial, LearningObject, LearningObjectGroup, OutlineNode


class PublishVersionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="teacher", password="pw", role="TEACHER"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.node = OutlineNode.objects.create(
            course=self.course, title="Matter", order=0, depth=0
        )
        self.material = LearningMaterial.objects.create(
            course=self.course,
            outline_node=self.node,
            title="Lesson 1",
            generated_json={"learning_objects_confirmed": True},
        )
        group = LearningObjectGroup.objects.create(outline_node=self.node)
        self.obj = LearningObject.objects.create(
            material=self.material,
            group=group,
            title="Solid",
            content="A solid holds its shape and does not flow away.",
            order=0,
        )

    def _publish(self):
        return self.client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/publish/"
        )

    @patch("lessons.views.generate_material_audio_playlist")
    @patch("course.variant_generator._request_variants")
    def test_publish_reports_incomplete_versions(self, request_variants, audio):
        audio.return_value = {"generated_count": 0}
        request_variants.side_effect = Exception("model offline")

        response = self._publish()

        self.assertEqual(response.status_code, 200)
        self.assertIn(self.obj.id, response.data["publish"]["incomplete_versions"])

    @patch("lessons.views.generate_material_audio_playlist")
    @patch("course.variant_generator._request_variants")
    def test_already_settled_content_generates_nothing(self, request_variants, audio):
        audio.return_value = {"generated_count": 0}
        for slot in ("SIMPLIFIED", "ELABORATED"):
            LessonVariant.objects.create(
                learning_object=self.obj, variant=slot, narration=f"{slot} text", origin="source_pdf"
            )

        response = self._publish()

        request_variants.assert_not_called()
        self.assertEqual(response.data["publish"]["incomplete_versions"], [])
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `backend/`:
```
..\.venv\Scripts\python.exe manage.py test lessons.test_publish_versions --noinput
```
Expected: FAIL — `KeyError: 'incomplete_versions'`

- [ ] **Step 3: Replace the generation call in `publish_topic`**

In `backend/lessons/views.py`, replace these two lines (currently 872-873):

```python
        sync_course_outline(course.id)
        variant_result = generate_standalone_variants(node)
```

with:

```python
        # Assignment normally happened at review; this is a backstop for content
        # edited afterwards. Settled groups generate nothing.
        sync_course_outline(course.id)
        variant_generated = []
        variant_errors = []
        for group in node.learning_object_groups.all():
            outcome = settle_group(group)
            variant_generated.extend(outcome["generated"])
            variant_errors.extend(outcome["errors"])

        # Checked in Python rather than with a queryset: "has both slots" needs
        # two independent joins on the same reverse relation, which a single
        # exclude() cannot express correctly.
        incomplete_versions = []
        for candidate in LearningObject.objects.filter(
            material__outline_node=node,
            represented_by__isnull=True,
        ).prefetch_related("variants"):
            if not (candidate.content or "").strip():
                continue
            slots = {row.variant for row in candidate.variants.all()}
            if not {"SIMPLIFIED", "ELABORATED"}.issubset(slots):
                incomplete_versions.append(candidate.id)
```

- [ ] **Step 4: Update the response payload**

In the same method, replace the three `adaptive_*` keys in the `payload["publish"]` dict:

```python
            "adaptive_variants_generated": len(variant_generated),
            "adaptive_variant_errors": variant_errors,
            "incomplete_versions": incomplete_versions,
```

Remove the now-unused import of `generate_standalone_variants` if nothing else references it, and add:

```python
from course.version_assignment import settle_group
```

Check for other references first:

```bash
grep -rn "generate_standalone_variants" backend/
```

`backend/course/tests.py` and `backend/lessons/tests.py` reference it. Leave the function in place; only the `views.py` call site changes. `backend/lessons/tests.py:1327` patches `lessons.views.generate_standalone_variants` and will fail once the import is gone — update that test to patch `lessons.views.settle_group` and assert on the new payload keys.

- [ ] **Step 5: Run the test to verify it passes**

Run from `backend/`:
```
..\.venv\Scripts\python.exe manage.py test lessons.test_publish_versions --noinput
```
Expected: PASS, 2 tests.

- [ ] **Step 6: Run the full suite**

Run from `backend/`:
```
..\.venv\Scripts\python.exe manage.py test lessons course --noinput
```
Expected: OK. If `lessons.tests.PublishTopicTests` fails, it is the patch target noted in Step 4 — fix it there.

- [ ] **Step 7: Commit**

```bash
git add backend/lessons/views.py backend/lessons/tests.py backend/lessons/test_publish_versions.py
git commit -m "Make publish a backstop for version assignment"
```

---

## Verification on real data

After Task 8, confirm against the actual course. Run from `backend/`:

```
..\.venv\Scripts\python.exe -c "import os,django;os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');django.setup();from lessons.models import LearningObject;from course.models import LessonVariant;print('objects',LearningObject.objects.count());print('teaching steps',LearningObject.objects.filter(represented_by__isnull=True).count());print('variants by origin',dict((o,LessonVariant.objects.filter(origin=o).count()) for o in ('source_pdf','generated')))"
```

Expected shape, given six pairs of which two classify confidently: 69 objects, 67 teaching steps (two partners absorbed), and at least two `source_pdf` variants. The four unconfident pairs stay unabsorbed until a teacher rules on them in the review screen.

## Out of scope

Do not touch, and do not "fix while you're here":

- `backend/adaptive/services.py` — the escalation ladder.
- `INSTALLED_APPS` / `learning_path` wiring.
- The `0.60` semantic grouping threshold or its calibration.
- Audio generation for the new rungs.
- The frontend review UI. This plan delivers the API; the screen is separate work.
