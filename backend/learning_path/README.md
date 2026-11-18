# learning_path

One learning path per **topic**: the order its concepts are taught in, and which concepts must come
before others. The path is saved when a teacher publishes the topic, and read by the adaptive rules.

How the links and the order are decided is in `CRITERIA.md`.

## Pipeline

```
Groups of learning objects across a topic's PDFs
   -> concepts in document order: follow the reference PDF     (services/concept_units.py)
   -> words: sentences, Porter stems, names, headings           (services/concept_text.py)
   -> owned words: SciPy G-test, p < .05                         (services/clues.py)
   -> a link per pair of concepts (v7.0):                        (services/criteria.py,
        the text says whether: a name, two owned words, a heading     clues.py, fusion.py)
        the lesson's order says which way
        nothing contradicts it -> accepted; contradicted -> pending
   -> stored, keeping every teacher decision;                   -> ConceptPrerequisite
      a link that would close a loop is held as pending          (services/publishing.py)
   -> at a successful publish: Kahn's sort over accepted +      -> LearningPathStep
      approved links, min-heap on document position, levels
   -> read by the adaptive rules                                 (services/published.py,
                                                                  GET topics/<id>/published/)
```

The review screen re-derives the links each time it opens, then previews the order publishing would
save (`services/path_builder.py`, `GET topics/<id>/`). Its columns are the path's levels. Pending
links are shown as suggestions and never shape the order.

Links across topics (the course-level path) are deferred to the 100% version. Their code was removed
on 2026-10-20 and can be restored from commit `5e45893`; the `CourseConceptLink` table is kept so the
database needs no migration.

## Settings

`config/settings.py`: `LEARNING_PATH_MIN_SENTENCE_WORDS` (4), `LEARNING_PATH_MIN_TERM_LETTERS` (3),
`LEARNING_PATH_MAX_NAME_TERMS` (6), `LEARNING_PATH_MIN_SHARED_TERMS` (2), and the questions each
published step carries, `LEARNING_PATH_QUESTIONS_LOT` (2) and `LEARNING_PATH_QUESTIONS_HOT` (1).
The owned-word significance level, p < .05, is `clues.SIGNIFICANCE_LEVEL`.

## Endpoints

| Method | Path | Who | Returns |
|---|---|---|---|
| GET | `/api/learning-path/topics/<id>/` | teachers/admins | re-derives the links; preview of the path |
| GET | `/api/learning-path/topics/<id>/published/` | signed-in users | the saved path; answers for teachers/admins only |
| POST | `/api/learning-path/topics/<id>/links/` | teachers/admins | add a "needs first" link (approved); refuses loops; returns the preview |
| POST | `/api/learning-path/topics/<id>/links/move/` | teachers/admins | make one concept the only prerequisite of another |
| POST | `/api/learning-path/topics/<id>/links/<link_id>/decision/` | teachers/admins | `{"status": "approved"\|"rejected"}`; a rejected link never comes back |
| POST | `/api/learning-path/topics/<id>/links/restore/` | teachers/admins | undo a link change |

Teacher link changes show in the preview at once but reach students only at the next successful
publish; the preview flags `changed_since_publish` until then.

## Commands

```bash
python manage.py show_learning_path [topic_id] [--preview] [--links] [--trace]
```

`--trace` derives the links without saving them and prints every step: the concepts, owned words,
each pair's facts and verdict, and the order.

## History

The first version ordered each PDF separately, with its own strong/medium/weak evidence rules and a
`PrerequisiteEdge` table between learning objects. It was removed on 2026-09-14 once the topic-level
path replaced it. The criteria in use (v7.0) and the changes up to the code freeze on 2026-11-19 are
described in `CRITERIA.md`.
