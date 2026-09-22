# Learning path — handoff for the adaptive rules

For whoever builds the student-facing rules and interaction. This explains
**what the learning path is, how to get it, what every field means, and what
is not your job to recompute.**

Last updated: 2026-09-14.

---

## 1. What you get

For each **subtopic** (an `OutlineNode`, e.g. *Solid, Liquid and Gas*), one
learning path:

- **Steps in teaching order.** Each step is one **concept**, assembled from
  every uploaded PDF that teaches it.
- **Everything a step teaches:** the Normal, Simplified and Elaborated versions,
  and the generated questions.
- **Prerequisites:** for each step, which earlier steps a student needs first.
  These are what you use to send a struggling student back.

The path is **saved when a teacher publishes the subtopic successfully**, and
replaced on the next successful publish. You only ever read it; you never
build it.

**Teachers can change prerequisites** on the review screen (add, remove, approve
suggestions). Those changes do **not** reach the saved path until the subtopic
is published again, so what you read is always a complete, published snapshot —
never a half-edited one.

---

## 2. How to get it

### From a frontend or any app — the API

```
GET /api/learning-path/topics/<outline_node_id>/published/
Authorization: Token <user token>      (or a signed-in session)
```

| response | when |
|---|---|
| `200` + the path | the subtopic has been published |
| `401` / `403` | not signed in |
| `404` `{"detail": "This topic has no published learning path yet. Publish it first."}` | never published, or unknown topic |

**Answers are role-dependent.** Teachers and admins receive each question's
`correct_answer` and `explanation`; **students do not** — so an answer key never
reaches a student's device. `includes_answers` in the response says which you got.

### From Python inside the backend — no HTTP

```python
from learning_path.services import get_published_path
from lessons.models import OutlineNode

path = get_published_path(OutlineNode.objects.get(pk=2))          # includes answers
path = get_published_path(node, include_answers=False)             # student-safe
if path is None:
    ...  # not published yet
```

Both return **exactly the same shape**; the API is a thin wrapper around this
function. Use the function for server-side grading (you need the answers), the
API for anything running in a browser.

> **Do not read the tables directly.** The response shape is the agreement; the
> tables behind it may change. If you need something the response lacks, ask for
> a field to be added.

---

## 3. The response

```json
{
  "topic": {"id": 2, "title": "Solid, Liquid and Gas"},
  "published": true,
  "published_at": "2026-09-14T13:40:02.118Z",
  "includes_answers": false,
  "steps": [
    {
      "position": 2,
      "depth": 1,
      "concept_id": 3,
      "title": "Solid",
      "section_title": "Solids",
      "learning_object_id": 3,
      "sources": [
        {"material_id": 1, "title": "Lesson-1_Solid-Liquid-and-Gas"},
        {"material_id": 2, "title": "states-of-matter-accessible"}
      ],
      "versions": {
        "normal":     {"text": "A solid has a definite shape and a definite volume…", "audio_url": ""},
        "simplified": {"text": "A solid keeps its shape…", "audio_url": "/media/…"},
        "elaborated": {"text": "A solid is a state of matter in which…", "audio_url": "/media/…"}
      },
      "questions": [
        {
          "id": 41,
          "text": "Does a solid keep its shape when moved to a new container?",
          "format": "TF",
          "choices": null,
          "bloom_level": "remember",
          "thinking_order": "LOT",
          "difficulty": "easy",
          "category": "Facts and Information"
        }
      ],
      "alternates": [
        {
          "learning_object_id": 9,
          "material_id": 2,
          "material_title": "states-of-matter-accessible",
          "title": "Solid",
          "versions": { "normal": { "...": "..." }, "simplified": null, "elaborated": null },
          "questions": [ { "...": "..." } ]
        }
      ],
      "prerequisites": [2],
      "leads_to": [15]
    }
  ]
}
```

### Step fields

| field | meaning |
|---|---|
| `position` | 1-based teaching order. **Teach in this order.** |
| `depth` | the longest chain of prerequisites leading here. `0` = needs nothing. Steps with the same depth don't depend on each other. |
| `concept_id` | stable id of the concept for this publish. `prerequisites` / `leads_to` refer to these. |
| `title`, `section_title` | display name, and the lesson heading it sits under (may be empty). A split passage is named without its "(Part 1 of 2)" suffix. |
| `learning_object_id` | the object holding the Normal text -- for a split passage, its first part. Versions and questions cover **every part** of the passage, not just this object. |
| `sources` | which uploaded PDFs contributed |
| `versions.normal` | always present |
| `versions.*.parts` | *(mavia addition)* one `{text, audio_url}` per chunk of the passage, in reading order. **Play these.** The chunker cuts an oversized passage into "(Part 1 of 2)" pieces; publishing merges them into one concept, but the step only records the first piece's group, so reading that group alone dropped every later part -- and, when question generation put the concept's questions on a later part, left the step with none, which made the engine skip it. `text` is the whole passage; `audio_url` is only filled when one recording covers all of it. A simplified/elaborated rung is `null` unless every part has one. |
| `versions.simplified` / `.elaborated` | `null` if missing. A successful publish requires both, so on a published path they are normally present — still handle `null`. |
| `questions` | at most 2: the earliest-generated LOT and earliest-generated HOT question on this node (LOT first), drafts never appear. A step is one assessment, not a quiz bank -- capped here even if question generation left more than one final row per `thinking_order` on a node (seen on real data: 3-4 final rows on one concept). |
| `alternates` | *(mavia addition, not upstream)* other uploaded PDFs' own independent take on this concept — same shape as the step itself (`versions` + `questions`), keyed to their own `learning_object_id`. Empty when only one PDF taught this concept. `mavia`'s adaptive engine reaches for one of these when re-explaining the representative's own text at every level hasn't worked; see `adaptive/PATH_MODE.md`. |
| `prerequisites` | `concept_id`s a student needs **first**, in path order |
| `leads_to` | `concept_id`s that build on this one |

### Question fields

| field | values |
|---|---|
| `format` | `"MCQ"` or `"TF"` |
| `choices` | MCQ: `{"A": "...", "B": "...", ...}`; TF: `null` |
| `correct_answer` | *(teachers/admins and Python only)* MCQ: the letter, e.g. `"B"`; TF: `"True"` / `"False"` |
| `explanation` | *(teachers/admins and Python only)* may be empty |
| `bloom_level` | `remember`, `understand`, `apply`, `analyze`, `evaluate`, `create` |
| `thinking_order` | `LOT` (lower-order) or `HOT` (higher-order) |
| `difficulty` | `easy`, `medium`, `hard` — derived from Bloom level, kept for step-down remediation |

---

## 4. How to use it in the rules

A sketch, not a requirement — the rules are yours:

1. **Walk `steps` by `position`.**
2. **Pick the version** for the student's level: `normal`, `simplified` or
   `elaborated`.
3. **Ask that step's `questions`**, e.g. LOT before HOT.
4. **On repeated failure, remediate through `prerequisites`:** send the student
   to the listed concepts (nearest first), then return. A step with an empty
   `prerequisites` list has nothing to remediate through — re-teach it with a
   simpler version instead.
5. **Steps with equal `depth` are independent**, so if you ever allow choosing
   the next step, any of them is valid.

---

## 5. What the path guarantees, and what it doesn't

**Guaranteed**

- Every prerequisite appears **before** the step that needs it, and there are no
  loops — the review screen refuses a change that would create one.
- Only prerequisites the criteria accepted and a teacher did not remove, or that
  a teacher added or approved, are included. Removed links and unreviewed
  suggestions never reach you.
- Content matches what was published: the path is saved only when the whole
  publish succeeded.

**Not guaranteed**

- **Ids stay the same across publishes.** A teacher regrouping content can
  change a `concept_id`. If you store student progress, store it against
  `concept_id` **and** `published_at`, and handle a concept that no longer
  exists after a republish.
- **Every step has prerequisites.** Many don't; the order still comes from the
  lesson files.
- **Accuracy is perfect.** A blind hand-check on one subtopic found 17 of 18
  automatically accepted prerequisites correct. That is one lesson; treat the
  links as good, not infallible.

---

## 6. Things that are not in the path (yet)

- **Student progress / mastery.** That is the adaptive side's to store.
- **Module order.** Paths are per subtopic; module and subtopic order comes from
  the course outline (`OutlineNode.order`, `parent`).
- **The existing student apps don't read the path.** `adaptive/services.py` and
  `adaptive_portal/services.py` still walk learning objects in PDF order. Switching
  them over to this path is part of the interaction work.

---

## 7. Trying it locally

```bash
cd backend
python manage.py migrate
python manage.py show_learning_path              # list subtopics
python manage.py show_learning_path 2            # the saved path, with prerequisites
python manage.py show_learning_path 2 --links    # every stored link and its status
python manage.py show_learning_path 2 --preview  # what the next publish would save
```

A subtopic has a saved path only after a successful publish from the teacher
review screen (step 5 → Publish).

---

## 8. Where things live (for reference, not for reading directly)

| what | where |
|---|---|
| API view | `learning_path/views.py` → `published_learning_path` |
| teacher link changes (not for the adaptive side) | `learning_path/services/teacher_links.py`, `POST topics/<id>/links/…` |
| the contract function | `learning_path/services/published.py` → `get_published_path` |
| saving at publish | `learning_path/services/publishing.py`, called from `lessons/services/topic_publish.py` |
| ordering and criteria | `learning_path/services/concept_units.py`, `criteria.py` — rules in `CRITERIA.md` |
| tables | `LearningPathStep` (steps), `ConceptPrerequisite` (links + teacher decisions) |
| tests for this contract | `learning_path/tests.py` → `PublishedPathTests` |
