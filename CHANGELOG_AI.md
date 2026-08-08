# CHANGELOG_AI.md

AI-generated change history for MAVIA. Newest first. Each entry records what
changed, why, and anything a future session needs to know that the diff alone
doesn't convey.

---

## 2026-08-09 — Bloom's → LOT/HOT reclassification + two-phase question pipeline

**Scope:** `backend/question_generation/` (owner's area), plus the minimum
compatibility edits needed elsewhere to keep the backend running.

### 1. Difficulty replaced with thinking order

**Why:** Bloom's Taxonomy describes the *kind of thinking* a question demands,
not how difficult it is. Mapping it onto easy/medium/hard conflated two
different concepts — flagged in review by the project's professor.

- `bloom_classifier.py`: `BLOOM_TO_DIFFICULTY` → `BLOOM_TO_THINKING_ORDER`
  - `remember / understand / apply → LOT`
  - `analyze / evaluate → HOT`
  - `create → None` (excluded; MCQ/TF cannot assess a "produce something new" task)
  - `classify()` now returns `thinking_order` instead of `difficulty`.
  - `create` is mapped to `None` rather than dropped from the dict on purpose:
    it is a real class the model predicts, so it must stay recognised by
    `_normalize_level()`. `None` means "no assessable thinking order".
- `models.GeneratedQuestion`: dropped `difficulty`, `intended_difficulty`,
  `difficulty_match`; added `thinking_order` (LOT/HOT). `category` kept —
  it is a separate Bloom-derived axis, not a difficulty restatement.

**Trade-off accepted:** dropping `intended_difficulty`/`difficulty_match`
removes the intended-vs-classified mismatch-rate audit trail that earlier
thesis write-ups cited. Nothing else consumed those fields.

### 2. Pipeline restructured: generate → save drafts → deterministic post-pass

**Why:** generation was too slow. The old loop classified every candidate
inline and then issued *more* LLM calls (up to 3 rebalance rounds with
"strict" prompt variants) whenever a difficulty bucket came up short.

- `GeneratedQuestion.status` (`draft`/`final`) added.
- `pipeline.py` split into two phases:
  - `_draft_questions_for_node()` — all LLM calls, results written straight to
    the DB as drafts. No classification.
  - `finalize_node_questions()` — deterministic, no LLM: deduplicate → classify
    → exclude `create` → trim surplus → atomically promote survivors.
- Dedup (`_dedup_key()`: lowercase, strip punctuation, collapse whitespace) is
  **new** — the old pipeline had none. It runs before classification so
  identical text isn't classified twice.
- Removed: `STRICT_PROMPT_TEMPLATES`, `MAX_REBALANCE_ROUNDS`, the rebalance
  loop, `_classify_and_tag`, `_difficulty_shortfall`, `_select_final_questions`,
  `save_node_questions` (replaced by `finalize_node_questions`).
- `QUESTION_DISTRIBUTION`: `5 easy + 5 medium + 3 hard (13)` → `5 LOT + 5 HOT (10)`.
- **LLM calls per node: 5–14 → 3.**

**Why dropping rebalancing is safe:** it existed to correct classifier drift
across bucket boundaries. With three buckets, an "easy" prompt drifting to
`apply` landed in *medium*, and a "hard" prompt drifting to `analyze` landed in
*medium* — both shortfalls. Under LOT/HOT those drifts land inside the intended
bucket. The one boundary that remains is `apply↔analyze`; `CLASSIFIER_STUDY_GUIDE.md`
notes `understand↔analyze` as a real confusion pair, so HOT can occasionally
come up short. Overgeneration (1.5×) absorbs this, and a shortfall now emits a
`shortfall_warning` event instead of costing extra LLM calls.

**Crash safety preserved:** the previous run's `final` rows are deleted only
inside the finalize transaction, so an interrupted run never leaves a node with
no questions. Orphaned drafts from a crashed run are cleared at the start of
the next one.

### 3. Compatibility edits outside question_generation

These were **required** — the dropped fields would otherwise raise
`FieldError`/`AttributeError` at import or request time:

- `serializers.py`, `views.py` (`GetQuestionView`, `QuestionStatsView`,
  `MaterialQuestionsView`, `QuestionDetailView`) — field renamed, `status="final"`
  filters added. `?difficulty=` → `?thinking_order=`; stats key
  `by_difficulty` → `by_thinking_order`.
- `lessons/services/audio_generator.py` — `_DIFFICULTY_ORDER` →
  `_THINKING_ORDER_RANK`, question queries filtered to `status="final"`.
- `course/services.py::sync_module_questions()` — added `status="final"` filter
  so drafts can never reach a learner. The adaptive engine is otherwise
  untouched: it reads `bloom_level` via `_bloom_bucket()` and never used
  `difficulty`.

**Not touched (deliberately):** the frontend. `TopicDetailPage.jsx:231` renders
a `difficulty-pill` that will now show blank. It does not crash, and the
mobile app has zero references to difficulty.

### 4. Migration

`question_generation/migrations/0003_*.py` — schema change plus a hand-written
`RunPython` backfill (the auto-generated migration alone would have been
destructive):

- Existing rows would have defaulted to `status="draft"`, making all 13
  production questions invisible. The backfill sets them `final` and derives
  `thinking_order` from the `bloom_level` already stored — no re-classification.
- `create`-level rows are deleted (none existed).
- Verified on the real dev DB: 13 rows preserved → 9 LOT / 4 HOT, 0 stranded.
- `backend/db.sqlite3.pre-lothot-backup` holds the pre-migration database
  (the dropped columns are otherwise unrecoverable; `db.sqlite3` is gitignored).

### 5. Model/application compatibility — verified, no retraining

Checked `MAVIA_blooms_classifier_training.ipynb` against the deployed
`trained_model/roberta_blooms_final/config.json`:

| Checked | Result |
|---|---|
| Trained classes | 6: remember, understand, apply, analyze, evaluate, create |
| `create` present as a trained class | **Yes** (id 2) — left in the model, excluded at application level |
| Label encoding | `sorted(unique)` → alphabetical; `config.json` `id2label` matches exactly |
| App reads labels how | `model.config.id2label[pred_id]` — from the saved config, not hardcoded |
| SVM preprocessing | notebook `preprocess_text()` is character-identical to `_preprocess_for_svm()` |
| Model output used | argmax over logits only; probabilities are not consumed |
| Retraining needed for LOT/HOT | **No** — the model never predicted difficulty; LOT/HOT is derived after classification |

No mismatch was found between notebook and application.

### 6. Tests

`question_generation/tests.py` rewritten for the new shape. New coverage:
draft→final promotion and labelling, `create` exclusion, duplicate removal,
surplus trimming, previous-run replacement, audio-staleness marking.

Verified: `manage.py check` clean; **30/30 tests pass**.

**Not verified:** no end-to-end run against a live Ollama server was performed.
Real generation quality under the new LOT/HOT prompts is unconfirmed.
