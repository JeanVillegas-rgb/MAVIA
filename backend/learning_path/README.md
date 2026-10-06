# learning_path

One learning path per **topic**: the order its concepts are taught in, and
which concepts must come before others. The path is saved when a teacher
publishes the topic, and read by the adaptive rules.

How the links and the order are decided is in `CRITERIA.md`.

## Pipeline

```
Groups of learning objects across a topic's PDFs
   -> concepts, split passages joined, merged PDF order         (services/concept_units.py)
   -> prerequisite links, per pair of concepts (v6.1):          (services/criteria.py,
        the text says whether: a name, two owned terms, a heading     clues.py, fusion.py)
        the PDF order says which way
        nothing contradicts it -> accepted; contradicted -> pending
   -> stored, keeping every teacher decision                    -> ConceptPrerequisite
                                                                 (services/publishing.py)
   -> at a successful publish: Kahn's sort over accepted +      -> LearningPathStep
      approved links, ties in merged PDF order
   -> read by the adaptive rules                                (services/published.py,
                                                                 GET topics/<id>/published/)
```

The review screen re-derives the links each time it opens, then previews the
order publishing would save (`services/path_builder.py`, `GET topics/<id>/`).
Pending links are shown as suggestions and never shape the order.

Across topics, `services/course_criteria.py` proposes links from a concept to
one in an earlier topic of the outline. Every derived course link is stored as
pending; only a teacher's approval lets it reach learners
(`services/course_links.py`, `CourseConceptLink`).

## Endpoints

| Method | Path | Who | Returns |
|---|---|---|---|
| GET | `/api/learning-path/topics/<id>/` | teachers/admins | re-derives the links; preview of the path |
| GET | `/api/learning-path/topics/<id>/published/` | signed-in users | the saved path; answers for teachers/admins only |
| POST | `/api/learning-path/topics/<id>/links/` | teachers/admins | add "needs first" link (approved); refuses loops; returns the preview |
| POST | `/api/learning-path/topics/<id>/links/move/` | teachers/admins | make one concept the only prerequisite of another |
| POST | `/api/learning-path/topics/<id>/links/<link_id>/decision/` | teachers/admins | `{"status": "approved"\|"rejected"}`; reject = removed for good |
| POST | `/api/learning-path/topics/<id>/links/restore/` | teachers/admins | undo a link change |
| GET | `/api/learning-path/courses/<id>/` | signed-in users | the Course path page; re-derives the course links |
| POST | `/api/learning-path/courses/<id>/links/<link_id>/decision/` | teachers/admins | approve or reject a course link |
| POST | `/api/learning-path/courses/<id>/links/restore/` | teachers/admins | undo a course link change |

Teacher link changes show in the preview at once but reach students only at
the next successful publish; the preview flags `changed_since_publish` until then.

## Commands

```bash
python manage.py show_learning_path [topic_id] [--preview] [--links]
python manage.py import_hand_check <answers.json> [--dry-run]   # a teacher's yes/no answers on links
```

## History

The first version ordered each PDF separately, with its own strong/medium/weak
evidence rules and a `PrerequisiteEdge` table between learning objects. It was
removed on 2026-09-14 once the topic-level path replaced it. The criteria in
use (v6.2) are described in `CRITERIA.md`.
