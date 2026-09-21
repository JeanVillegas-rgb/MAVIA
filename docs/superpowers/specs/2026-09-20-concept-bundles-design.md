# Concept bundles: automatic grain matching without a merge step

Date: 2026-09-20 · Status: design approved in brainstorming, not implemented
Supersedes: the manual merge feature of
`2026-09-17-grouping-merge-and-learning-path-design.md` §4.2 (kept on branch
`current-with-merge-function`, pushed to origin). The learning-path criteria of
that spec (§4.4-§4.6) are unaffected and stay.

## 1. Problem

Two PDFs teach the same lesson at different grain. One file teaches Solid as a
single passage; the other teaches it as a section plus a particle diagram plus
an examples list. A concept (`LearningObjectGroup`) admits at most one object
per PDF (`semantic_grouping.semantic_decision`, `eligible_groups`), so those
three objects cannot join the concept their counterpart belongs to.

The merge feature solved this by joining the three objects into one row. It
works and is reversible, but:

- it is a document-structure decision handed to the teacher, who must tick
  objects and click Merge for every such case;
- it rewrites lesson text, so a wrong guess is destructive: undoing it
  regenerates versions and audio, and hand-edited version text is lost;
- it refuses when two of the ticked objects are already connected to another
  PDF, forcing a Separate-then-merge dance (measured on topic 109);
- the merged row's title and the concept label came from whichever object was
  first, which cost the learning path its edges until fixed.

The teacher's intent - "these three objects are the same teachable unit as that
one" - should be derived, not clicked.

## 2. Decisions taken (user, 2026-09-20)

- A run of objects under one heading is **not** joined into one object. The
  objects stay; the concept holds them as an ordered *bundle*.
- Bundles form automatically where another PDF corroborates them; uncertain
  cases still surface a suggestion card.
- Versions are classified **per bundle**; missing versions are generated **per
  object** inside the bundle.
- Wrong bundles are fixed with small controls in the existing Review
  Connections step, not a new screen and not drag-and-drop.
- No migration of existing merged rows: the user deletes the course and
  re-uploads once this ships.
- Storage approach: bundles are **derived**, not a new table.

## 3. Design

### 3.1 Bundles

A *bundle* is all objects of one concept that come from one PDF, in document
order. One helper is the only way to obtain them:

```
bundles_for_group(group) -> {material_id: [LearningObject, ...]}   # each list ordered by (order, id)
```

Every consumer - version assignment, audio, questions, the learning path, the
API - reads bundles through this helper, so ordering cannot drift between them.

The one-object-per-PDF restriction is removed from
`semantic_grouping.semantic_decision`'s `eligible_groups`.

### 3.2 How a bundle forms

1. **Runs inside a PDF**: two or more consecutive objects sharing a non-empty
   `section_title`, extended by an immediately following object whose
   numbering-stripped, singular-folded title equals that heading. This is
   today's `unit_matching.find_units`.
2. **Corroboration from another PDF**: the run becomes one concept only when
   another material of the topic holds a single object or a run whose
   normalized title/heading equals the run's label, confirmed by the
   cross-encoder. Structure alone over-merges: in
   `Lesson-1_Solid-Liquid-and-Gas` the "Matter" heading covers Matter, Solid,
   Liquid and Gas.
3. **Apply or ask**: at or above the auto threshold (0.6) with the configured
   margin, the objects are placed in the counterpart's concept automatically.
   Between the review threshold (0.3) and the auto threshold, a
   `LearningObjectMatchSuggestion` is raised carrying every member per side
   (`source_extra_ids` / `candidate_extra_ids`), and accepting it places the
   objects rather than merging them.
4. Objects no other PDF corroborates stay as their own concepts.

Measured on the current upload: Solid 0.651, Liquid 0.622, Gas 0.612 bundle
automatically; Comparing 0.556 and Everyday Examples 0.533 raise cards.

### 3.3 Versions

- The concept's original PDF supplies the **Normal** version: that bundle's
  objects, in order. Selection of the original is unchanged.
- Each other PDF's bundle is classified **as a whole** (readability over its
  combined text) into Simplified, Elaborated or Extra. The teacher may change a
  bundle's role, as today.
- **Storage**:
  - A version supplied by a PDF stores no copied text. The group records which
    material is Normal and the role of each other material's bundle.
  - A version that must be written is stored as today in `LessonVariant`, one
    row per object of the Normal bundle, in the same order.
- **Generation is per object** of the Normal bundle: calls stay as short as
  today, a model failure costs one object rather than a concept, and the
  existing staleness fingerprint still describes exactly one object.
- **Audio** is unchanged: clips are per object, and a version plays its
  objects' clips in order.
- The mobile `LessonPackage` returns a version as an ordered list.

### 3.4 Questions

Generated from the Normal bundle's text joined in order - the same text the
Normal track speaks. A question still links to the single object it came from,
so `QuestionLearningObjectLink` and `GeneratedQuestion.node` are unchanged and
questions follow their object when it moves.

### 3.5 Learning path

Unchanged except:

- `Concept.member_text` orders members by bundle, so each PDF's wording is read
  as a block;
- the concept's name comes from its bundle's first heading **only when the
  bundle holds more than one object**; a single-object bundle keeps that
  object's title. Amended 2026-09-20 after checking real data: in
  `Lesson-1_Solid-Liquid-and-Gas` the Solid, Liquid and Gas objects all sit
  under the heading "Matter", so the heading rule would name three concepts
  "Matter" and the criteria's same-name veto would delete their edges. A
  multi-object bundle still needs the heading (Shape + Volume + Particle
  arrangement + Flow -> "Comparing the Three States"; figure + Matter ->
  "Matter").

Criteria, vote, ordering, gold fixtures and the gold test stay as they are.

### 3.6 Teacher controls

In the existing Review Connections step, each object row gains:

- **Move out** - the object becomes its own concept;
- **Move to...** - choose another concept in the topic;
- **Up / Down** - reorder within its bundle.

Plain buttons only: keyboard and screen-reader usable, no drag-and-drop.
Connect and Separate remain; Connect joins two concepts with their bundles.

### 3.7 Removals

- `lessons/services/object_merge.py` and its tests
- the merge and split endpoints and their UI controls
- `LearningObject.merged_from` and a migration dropping the column
- the merge-on-accept path of `unit_matching`; unit detection itself is kept

## 4. Testing

1. `bundles_for_group`: ordering, several PDFs, single-object concepts.
2. Automatic bundling: Solids + diagram + examples bundles with Solid; the
   "Matter" heading does not fuse Matter, Solid, Liquid and Gas; a match below
   the auto threshold raises a card instead of bundling.
3. Version assignment: a three-object bundle is classified as one; a generated
   Simplified yields one row per Normal-bundle object in order; a teacher's
   role change survives a refresh.
4. Teacher controls: move out, move to, reorder; questions follow their object.
5. The gold test passes unchanged on both lessons.
6. Full backend suite; then a clean re-upload of both PDFs, with no teacher
   action except the uncertain cards, compared against the gold standard.

## 5. Risks

- `version_assignment.py` carries the one-object assumption throughout and is
  the most intricate file in the repo. Its failures are quiet: a wrong
  assignment reads as an odd lesson, not an error. Tests target it first.
- Automatic placement means a wrong bundle reaches the teacher as a fact rather
  than a question. It is non-destructive and reversible with Move out, but it
  is applied without asking.
- Expected outcome on the gold standard is unchanged (10/10 and 4/8 with the
  four known gaps). A different result means bundling produced different
  concepts than merging did, and is a finding, not a tuning target.
- Rollback: `origin/current-with-merge-function`.
