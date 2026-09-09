# learning_path

The **generic** (pre-adaptive) learning path: one deterministic sequence of
learning objects per learning material, identical for every student. The
adaptive layer will later reorder, skip, or repeat within this sequence; none of
that exists yet and nothing in this app is student-specific.

## Why this app exists

`LearningObject.order` is the source PDF's own paragraph sequence. Serving it
directly — which is what `course.services.LessonPackageService` does today — is a
passthrough of the document, not a derived path. This app produces the ordering
from a dependency graph instead.

## Pipeline

```
LearningObjects of one material
   -> text signals            (text_signals.py)
   -> prerequisite DAG        (edge_derivation.py -> PrerequisiteEdge rows)
   -> Kahn's topological sort (topological_sort.py)
   -> ordered path + diagnostics (path_builder.py)
```

### 1. Edge derivation (`edge_derivation.py`)

A short title (at most 4 words) is treated as *defining* a concept; long titles
are prose lifted from the PDF and define nothing. Four signals, strongest first:

| Signal | Weight | Meaning |
|---|---|---|
| `chunk_continuation` | 1.0 | "(Part 1 of 3)" -> "(Part 2 of 3)": one passage the chunker cut, kept adjacent and in order |
| `title_reference` | 1.0 | The dependent's body names a concept an earlier object defines |
| `definition_scope` | 0.8 | The material's opening definition grounds the definitions that follow it |
| `section_reference` | 0.6 | Only the dependent's `section_title` names it |
| `term_cooccurrence` | 0.4 | Fallback: an object no other signal reached is attached to the nearest earlier object sharing at least 3 *distinctive* terms |

Two of those need explaining, because both exist to fix a specific way the
graph got the lesson wrong.

**`definition_scope`.** "Matter" is what solid, liquid and gas are kinds of, but
the passage "A solid has a definite shape and a definite volume" never says the
word *matter* -- so no reference signal can connect them, and the concept the
whole lesson rests on was left floating as an unconnected starting point. A
passage is recognised as a definition by the shape of its opening sentence
(`<subject> is|are|has|have|means|refers to ...`), which is why "Solids keep
their shape" does not count: it describes behaviour, it does not define. The
earliest definition in a material becomes a prerequisite of the later ones.

**Distinctive terms in `term_cooccurrence`.** Counting *how many* words two
passages share made Solid a prerequisite of Liquid and Liquid of Gas, on the
strength of "definite", "particle", "shape" and "together" -- the background
vocabulary of a states-of-matter lesson, present in nearly every passage. A term
now only counts when it is confined to a few passages of the material (see
`MAX_DISTINCTIVE_DOCUMENT_FREQUENCY_RATIO`). Three peer concepts therefore stay
peers instead of being strung into a false chain.

Two guards keep the graph honest:

- **Co-definers.** Objects whose titles claim the same concept (chunker splits, a
  second teacher upload) never become prerequisites of one another — except via
  `chunk_continuation`, which is exactly the case where the order does matter.
- **Forward orientation.** An edge may only run forward through the document. A
  passage that uses a word the material does not define until later is using it
  loosely, not depending on it; recording that inverts the lesson. This also
  makes the edge set acyclic by construction — which Kahn's verifies rather than
  assumes, so a hand-authored edge added later cannot silently break the sort.

Edges are persisted as `PrerequisiteEdge` rows with their triggering `evidence`,
so the graph is inspectable and can be corrected by a teacher later.

### 2. Topological sort (`topological_sort.py`)

Kahn's algorithm: adjacency list, in-degree counter, and a frontier of nodes
whose prerequisites are all satisfied. Because several nodes are usually ready at
once, the frontier is drained in a fixed priority rather than arbitrary arrival
order:

1. lower DAG depth first (teach foundations first; this is what regroups content
   into dependency layers and makes the result differ from document order)
2. then higher **mean incoming edge confidence** — within one layer, the nodes
   whose prerequisites are best evidenced come before ones attached by weaker
   signals, so a mis-derived edge does its damage late rather than early. The
   mean, not the sum: a sum would only re-measure prerequisite count, which is
   already rule 4. A node with no prerequisites scores 1.0 — a root is not
   weakly supported, it needs no support.
3. then earlier first mention in the source text
4. then fewer prerequisites
5. then object id, so the result is never ambiguous

Confidence never overrides a dependency. A weak edge is still a hard constraint;
it only decides the order among nodes that are already free to be taught.

DAG depth is the longest path from a root, finalised the moment a node's last
prerequisite is emitted. A cycle raises `GraphCycleError` naming the nodes that
never reached in-degree zero.

## Usage

```python
from learning_path.services import build_learning_path

result = build_learning_path(material_id)            # derives edges if missing
result = build_learning_path(material_id, rebuild=True)  # re-derive first

result["learning_path"]   # [learning_object_id, ...] -- the path
result["steps"]           # per step: depth, prerequisites, source position
result["diagnostics"]     # node/edge counts, max depth, matches_source_order
```

HTTP: `GET /api/learning-path/materials/<material_id>/` returns the same payload;
`POST` to the same URL re-derives the edges first.

## Teacher review and editing

Topic review step 3 of 4 (`frontend/src/components/LearningPathPanel.jsx`) shows the
derived order, each step's dependency layer, and the evidence behind every
prerequisite. Publishing is step 4 and unlocks only once the teacher ticks the
verification box; any edit to the graph clears that tick.

Because the order is *derived*, editing works on the reasons rather than on the
sequence: remove a prerequisite the derivation got wrong, or add one it missed,
and the path re-sorts. Teacher edges are `source="teacher"` with weight 1.0 and
survive re-derivation — a correction should not be undone by reprocessing. An
edge that would close a cycle is refused and rolled back, so the teacher is told
which objects are caught in the loop instead of being left with a material that
has no producible path. This is where Kahn's cycle check stops being decorative.

| Endpoint | Does |
|---|---|
| `GET /api/learning-path/topics/<node_id>/` | Every path under one topic, one per material |
| `POST /api/learning-path/materials/<id>/` | Re-derive from text, keeping teacher edges |
| `POST /api/learning-path/edges/` | Add a teacher prerequisite; 409 if it would loop |
| `DELETE /api/learning-path/edges/<id>/` | Remove a prerequisite, derived or teacher |

## Not wired into the learner flow yet

`LessonPackageService.build_package()` still emits chunks in `order, id`.
Swapping that for `build_learning_path(...)["learning_path"]` is a one-line
change in `course/services.py`, deliberately left out of this milestone because
it changes what learners actually see.

## Not in scope (later milestones)

No BKT mastery tracking, no escalation/remediation, no DQN node selection. This
is only the deterministic scaffold those will modify per student.
