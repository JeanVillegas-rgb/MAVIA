# Question Generation Pipeline — Execution Trace

Precise, line-numbered trace of the question-generation stage for thesis
defense reference. Covers one question's full lifecycle: HTTP trigger →
LLM call → classification → filtering → rebalancing → trimming → DB save.
Cross-referenced against the code as of this writing — if line numbers drift
after a refactor, re-check against the source before citing this document.

**Scope:** upstream PDF extraction / lesson-content generation
(`lessons/services/content_generator.py`) and downstream learner-facing
serving are out of scope — this covers `question_generation/` only, from a
`LearningObject` already in the DB to a saved `GeneratedQuestion`.

All file paths below are relative to `backend/question_generation/` unless
stated otherwise (upstream models live in `backend/lessons/models.py`).

---

## Execution trace

### Step 1: Trigger — teacher/operator starts a run

**File:** `views.py` → `StartGenerationView.post()` (lines 172–235)

A teacher-facing POST validates the material is ready, refuses to start a
second concurrent run, then hands off to a background thread so the HTTP
request returns immediately instead of blocking for the whole run.

```python
# views.py:226-231
run = GenerationRun.objects.create(material=material, node=node)
threading.Thread(
    target=_run_pipeline,
    args=(run.id, material.id, [node.id] if node else None),
    daemon=True,
).start()
```

### Step 2: Background thread entry point

**File:** `views.py` → `_run_pipeline()` (lines 141–169)

Runs off the request thread; wraps the whole pipeline in a try/except so any
uncaught exception still marks the run `failed` instead of leaving it stuck
`running` forever.

```python
# views.py:157-165
try:
    material = LearningMaterial.objects.get(id=material_id)
    questions = generate_questions_for_material(
        material, on_event=on_event, node_ids=node_ids)
    on_event("saved", f"Saved {len(questions)} questions to database",
             {"count": len(questions)})
    GenerationRun.objects.filter(id=run_id).update(
        status="finished", finished_at=timezone.now())
```

### Step 3: Content loaded from DB

**File:** `services/pipeline.py` → `generate_questions_for_material()` (lines 338–409)

Pulls the material's text `LearningObject` rows straight from the DB —
there is no JSON handoff from the upstream content-generation stage, only
foreign keys. Empty-content nodes are skipped since there's nothing to
generate questions from.

```python
# services/pipeline.py:352-360
nodes_qs = (
    material.learning_objects
    .filter(kind="text")
    .exclude(content="")
    .order_by("order")
)
if node_ids is not None:
    nodes_qs = nodes_qs.filter(id__in=node_ids)
nodes = list(nodes_qs)
```

### Step 4: Per-node generation begins

**File:** `services/pipeline.py` → `generate_questions_for_material()` loop (lines 376–392)

Each `LearningObject` ("node") is generated and saved independently, so an
interrupted run keeps every node that already finished instead of losing
everything.

```python
# services/pipeline.py:376-383
for node in nodes:
    print(f"Generating questions for: {node.title}")
    _emit(
        on_event, "node_started", f"Generating questions for: {node.title}",
        node_id=node.id, title=node.title,
    )
    questions = generate_questions_for_node(node, classifier, on_event=on_event, stats=stats)
    created = save_node_questions(node, questions)
```

### Step 5: Prompt built for one difficulty × format

**File:** `services/question_generator.py` → `_build_prompt()` (lines 145–155), called from `generate_questions()` (line 302)

`content` (the node's raw text) and `difficulty` decide which template gets
used; `strict` (False on pass 1) picks between the normal and strict prompt
sets — see [Configuration → Prompt templates](#prompt-templates) below for
what actually differs between them.

```python
# services/question_generator.py:145-155
def _build_prompt(content, difficulty, format_type, count=1, strict=False):
    templates = STRICT_PROMPT_TEMPLATES if strict else PROMPT_TEMPLATES
    template = templates[difficulty]
    return template.format(
        count=count,
        content=content,
        format_type=format_type,
        format_instructions=FORMAT_INSTRUCTIONS[format_type],
        question_schema=QUESTION_SCHEMA[format_type],
        examples=FEW_SHOT_EXAMPLES[difficulty],
    )
```

### Step 6: LLM called via Ollama

**File:** `services/question_generator.py` → `_ollama_generate()` (lines 264–283)

A plain synchronous HTTP POST to the local Ollama server — no streaming,
no async — since the whole pipeline already runs off the request thread in
a background `threading.Thread`.

```python
# services/question_generator.py:266-281
response = requests.post(
    f"{settings.OLLAMA_BASE_URL}/api/generate",
    json={
        "model": settings.QUESTION_LLM_MODEL,
        "prompt": prompt,
        "stream": False,
        "keep_alive": settings.OLLAMA_KEEP_ALIVE,
        "options": {
            "temperature": 0.7,
            "num_predict": 2048,
        },
    },
    timeout=settings.OLLAMA_TIMEOUT,
)
response.raise_for_status()
return response.json()["response"]
```

### Step 7: Raw response parsed — regex + JSON extraction

**File:** `services/question_generator.py` → `_parse_llm_response()` (lines 230–261)

Local models wrap JSON in markdown fences or add preamble text, so the
fences are stripped and the outermost `{...}` span is regex-extracted
before parsing. If `json.loads` still fails (most often a truncated
response cut off by `num_predict`, or a missing comma between two question
objects), `_extract_question_objects()` (lines 188–227) salvages whichever
individual question objects *are* complete, instead of discarding the whole
batch over one bad object.

```python
# services/question_generator.py:239-254
text = re.sub(r"^```(?:json)?\s*", "", text)
text = re.sub(r"\s*```$", "", text)

match = re.search(r"\{.*\}", text, re.DOTALL)
if not match:
    raise ValueError(f"No JSON found in LLM response: {text[:200]}")

try:
    parsed = json.loads(match.group())
except json.JSONDecodeError:
    recovered = _extract_question_objects(match.group())
    if not recovered:
        raise
    print(f"  Recovered {len(recovered)} question(s) from malformed JSON response")
    return recovered
```

### Step 8: Structural validation — malformed answers rejected

**File:** `services/question_generator.py` → `_validate_question()` (lines 158–185)

Rejects a question outright if it's missing required keys, or if its
`correct_answer` isn't a real choice letter (e.g. the LLM hallucinates
`"A, B, C and D"` or answers with the choice's text instead of its letter —
the latter is recovered, not rejected). For TF, anything that doesn't
normalize to `"True"`/`"False"` is rejected.

```python
# services/question_generator.py:162-179
if "question" not in q or "correct_answer" not in q:
    return False

answer = str(q["correct_answer"]).strip()

if format_type == "MCQ":
    choices = q.get("choices")
    if not isinstance(choices, dict) or not choices:
        return False
    if answer in choices:
        q["correct_answer"] = answer
        return True
    # LLM sometimes answers with the choice text instead of the letter
    for letter, text in choices.items():
        if str(text).strip().lower() == answer.lower():
            q["correct_answer"] = letter
            return True
    return False
```

Rejected questions are silently dropped in the caller's loop
(`services/question_generator.py:311-312`, inside `generate_questions()`) —
they never reach classification.

### Step 9: Classification — `BloomClassifier.classify()`

**File:** `services/bloom_classifier.py` → `classify()` (lines 167–181), called from `pipeline.py` → `_classify_and_tag()` (line 72)

Every surviving question is classified independently of what difficulty the
prompt originally asked for. Backend is RoBERTa when available, falling
back to an SVM pipeline — the caller never knows which backend answered.

```python
# services/bloom_classifier.py:167-181
def classify(self, question_text: str) -> dict:
    if self.backend == "roberta":
        bloom_level = self._classify_roberta(question_text)
    else:
        bloom_level = self._classify_svm(question_text)

    bloom_level = self._normalize_level(bloom_level)

    return {
        "bloom_level": bloom_level,
        "thinking_order": BLOOM_TO_THINKING_ORDER[bloom_level],
        "category": BLOOM_TO_CATEGORY[bloom_level],
    }
```

### Step 10: Bloom level mapped to thinking order and category

**File:** `services/bloom_classifier.py` — module-level lookup tables (lines 7–23)

Fixed dicts turn the six-level Bloom classification into the two
representations the rest of the system actually uses: LOT/HOT (for
sequencing and item-bank balancing) and a 4-tier category (for thesis
reporting and the Course Builder). A third table, `BLOOM_TO_DIFFICULTY`,
used to sit alongside these and derive an easy/medium/hard label — removed
because nothing read it and difficulty is not soundly derivable from Bloom's
level in the first place. If you see any reference to `difficulty`,
`intended_difficulty`, or `difficulty_match` elsewhere in this document
below Step 10, treat it as stale: this whole doc predates that removal and
in several places (Steps 10–12) already didn't match `pipeline.py`'s actual
current shape even before it.

```python
# services/bloom_classifier.py:7-23
BLOOM_TO_CATEGORY = {
    "remember":    "Facts and Information",
    "understand":  "Meaning",
    "apply":       "Skills",
    "analyze":     "Skills",
    "evaluate":    "Outcome",
    "create":      "Outcome",
}
```

### Step 11: Question tagged — intended vs. classified recorded

**File:** `services/pipeline.py` → `_classify_and_tag()` (lines 70–86)

The prompt's intended difficulty and the classifier's actual difficulty are
recorded side by side, and compared once, right here — this is the single
source of truth for `difficulty_match` used everywhere downstream
(rebalancing, trimming, storage).

```python
# services/pipeline.py:74-85
q["node"] = node

# intended = what we asked the LLM for
q["intended_difficulty"] = intended_difficulty

# classified = what the classifier says (THIS is authoritative)
q["bloom_level"] = classification["bloom_level"]
q["difficulty"] = classification["difficulty"]
q["category"] = classification["category"]

# flag mismatches (useful for thesis analysis)
q["difficulty_match"] = q["intended_difficulty"] == q["difficulty"]
```

### Step 12: Create-level questions filtered out

**File:** `services/pipeline.py` → `_generate()` closure inside `generate_questions_for_node()` (lines 245–260)

MCQ and TF cannot assess "create" (design/construct/compose) — there's no
single gradeable answer — so these are dropped immediately after
classification, *before* they can count toward the "hard" quota (`create`
maps to `difficulty="hard"` in Step 10's table). Filtering here rather than
at final selection keeps the pass-1/rebalance shortfall math honest: a
create-classified question never silently satisfies a quota it can't
actually fill.

```python
# services/pipeline.py:245-260
for q in questions:
    tagged = _classify_and_tag(q, node, classifier, difficulty)
    filtered = tagged["bloom_level"] in UNASSESSABLE_BLOOM_LEVELS
    _print_question_block(tagged, fmt, filtered)
    if filtered:
        filtered_create_count += 1
        _emit(
            on_event, "question_dropped",
            tagged["question"],
            reason=f"{tagged['bloom_level']}-level question cannot be assessed by MCQ/TF",
            intended=difficulty,
            bloom_level=tagged["bloom_level"],
            format=fmt,
            strict=strict,
        )
        continue
    all_questions.append(tagged)
```

`UNASSESSABLE_BLOOM_LEVELS = {"create"}` (`services/pipeline.py:42`).

### Step 13: Pass 1 complete — distribution checked against quota

**File:** `services/pipeline.py` → `_difficulty_shortfall()` (lines 89–96)

After every difficulty × format combination in `QUESTION_DISTRIBUTION` has
been generated once (pass 1, `strict=False`), classified counts are
compared against the target. Anything below target is short; anything at
or above is left alone (surplus gets trimmed later, not discarded here).

```python
# services/pipeline.py:89-96
def _difficulty_shortfall(questions):
    """Compare classified difficulty counts against the target distribution."""
    counts = Counter(q["difficulty"] for q in questions)
    return {
        difficulty: config["count"] - counts.get(difficulty, 0)
        for difficulty, config in QUESTION_DISTRIBUTION.items()
        if counts.get(difficulty, 0) < config["count"]
    }
```

### Step 14: Rebalance triggers if a level is short

**File:** `services/pipeline.py` → `generate_questions_for_node()` pass-2 loop (lines 286–304)

Up to `MAX_REBALANCE_ROUNDS` extra rounds regenerate only the short
difficulty levels, using the **strict** prompt variant and rotating format
across rounds. The loop exits the moment nothing is short — it's not a
fixed number of rounds, it's a cap.

```python
# services/pipeline.py:287-303
for round_num in range(1, MAX_REBALANCE_ROUNDS + 1):
    shortfall = _difficulty_shortfall(all_questions)
    if not shortfall:
        break

    _emit(
        on_event, "rebalance_round",
        f"Round {round_num}/{MAX_REBALANCE_ROUNDS}: regenerating with strict prompts",
        round=round_num, shortfall=shortfall,
    )
    for difficulty, needed in shortfall.items():
        formats = QUESTION_DISTRIBUTION[difficulty]["formats"]
        # one batched LLM call per short level, rotating format across rounds
        fmt = formats[(round_num - 1) % len(formats)]
        print(f"Rebalance round {round_num}/{MAX_REBALANCE_ROUNDS} — targeting: {difficulty}")
        print(f"Using strict prompt ({_STRICT_PROMPT_HINTS.get(difficulty, 'stricter constraints')})")
        _generate(difficulty, fmt, needed, strict=True)
```

Each `_generate(..., strict=True)` call re-enters Steps 5–13 (prompt →
Ollama → parse → validate → classify → tag → filter) exactly as pass 1 did,
just with `STRICT_PROMPT_TEMPLATES` instead of `PROMPT_TEMPLATES`. If still
short after all rounds, `_emit(on_event, "shortfall_warning", ...)`
(`services/pipeline.py:307-312`) fires and the node proceeds with whatever
it has — one stubborn node never blocks the rest of the material.

### Step 15: Final set trimmed to target counts

**File:** `services/pipeline.py` → `_select_final_questions()` (lines 202–214)

Per classified difficulty, keeps only the configured `count`, preferring
questions whose intended difficulty matched their classification (a stable
sort puts matches first, so ties break toward "the LLM got what it was
asked for"). Rebalancing surplus and pass-1 overgeneration are dropped
here, not stored.

```python
# services/pipeline.py:202-214
def _select_final_questions(questions):
    final = []
    for difficulty, config in QUESTION_DISTRIBUTION.items():
        bucket = [q for q in questions if q["difficulty"] == difficulty]
        bucket.sort(key=lambda q: not q["difficulty_match"])  # stable: matches first
        final.extend(bucket[:config["count"]])
    return final
```

### Step 16: Questions saved atomically — old rows deleted first

**File:** `services/pipeline.py` → `save_node_questions()` (lines 412–469), transaction block (lines 456–465)

The node's entire previous question set is deleted, then the new curated
set is bulk-created, inside one `transaction.atomic()` block — the DB never
observes a state with zero questions for a node mid-regeneration, and never
holds a mix of an old run's and a new run's rows.

```python
# services/pipeline.py:456-465
with transaction.atomic():
    deleted, _ = GeneratedQuestion.objects.filter(node=node).delete()
    created = GeneratedQuestion.objects.bulk_create(db_objects)
    material = node.material
    generated_json = material.generated_json or {}
    if generated_json:
        generated_json["question_audio_generated"] = False
        generated_json["audio_playlist_generated"] = False
        material.generated_json = generated_json
        material.save(update_fields=["generated_json"])
```

The `.delete()` on line 457 cascades to `LearnerResponse` — see
[Database → cascade behavior](#d-cascade-behavior) below. This is a
deliberate design decision, not an oversight: regenerating a node's
questions is expected to reset that node's learner history.

### Step 17: Trace events persisted for the live progress feed

**File:** `views.py` → `_run_pipeline()`'s `on_event` closure (lines 147–155)

Every `_emit(...)` call throughout Steps 1–16 (question_generated,
question_dropped, rebalance_round, node_trimmed, node_finished,
material_finished, …) is a no-op unless a callback is wired in; here it's
wired to write one `GenerationEvent` row per call, so the frontend can poll
mid-run instead of only seeing a final result.

```python
# views.py:145-155
seq_counter = [0]

def on_event(event_type, message, data):
    seq_counter[0] += 1
    GenerationEvent.objects.create(
        run_id=run_id,
        seq=seq_counter[0],
        event_type=event_type,
        message=message,
        data=data,
    )
```

### Step 18: Run marked finished — frontend polls the result

**File:** `views.py` → `_run_pipeline()` tail (lines 164–165) and `GenerationTraceView.get()` (lines 388–428)

Once every node in the material has been generated and saved (Steps 3–17
looped per node), the `GenerationRun` row flips to `"finished"`. The
frontend's poll loop (`GET /api/generation/runs/<run_id>/events/?after=<seq>`)
picks this up the same way it picked up every event along the way — by
polling `seq__gt=after`.

```python
# views.py:164-165
GenerationRun.objects.filter(id=run_id).update(
    status="finished", finished_at=timezone.now())
```

---

## Configuration

**File:** `services/pipeline.py:20-51`

```python
QUESTION_DISTRIBUTION = {
    "easy":   {"count": 5, "formats": ["MCQ", "TF"]},
    "medium": {"count": 5, "formats": ["MCQ", "TF"]},
    "hard":   {"count": 3, "formats": ["MCQ"]},
}
# 13 questions per node after trimming

OVERGENERATION_FACTOR = 1.5   # pass-1 asks for count × 1.5, split across formats
MAX_REBALANCE_ROUNDS = 3      # cap on pass-2 retry rounds
UNASSESSABLE_BLOOM_LEVELS = {"create"}   # dropped, never fills a quota
```

**LLM call** (`services/question_generator.py:264-283`, `config/settings.py:129`):

| Setting | Value |
|---|---|
| Model | `QUESTION_LLM_MODEL` = `"llama3.2:3b"` (env-overridable) |
| Temperature | `0.7` |
| `num_predict` | `2048` (raised from 1024 — a 5-question hard-MCQ batch can exceed 1000 tokens and get cut off mid-JSON) |
| `max_retries` | `3` (per `generate_questions()` call, `services/question_generator.py:286`) |
| Ollama endpoint | `{OLLAMA_BASE_URL}/api/generate`, default `http://localhost:11434` |

### Prompt templates

Normal prompts (pass 1) state the target cognitive level loosely. Strict
prompts (rebalance only) explicitly ban the phrasing that causes drift.
Example — `medium` (`services/question_generator.py:25-35` vs `68-83`):

```python
# PROMPT_TEMPLATES["medium"] (normal, pass 1)
"The questions should present a SCENARIO or ask the learner to COMPARE, "
"DIFFERENTIATE, or APPLY a concept from the content to a new situation.\n\n"
```

```python
# STRICT_PROMPT_TEMPLATES["medium"] (rebalance only)
"Each question MUST present a short concrete scenario and ask the learner "
"to APPLY a concept from the content to predict what happens.\n"
"Use stems like \"What happens when...\" or \"A student does X. What will result?\".\n"
"Do NOT ask for simple recall of a stated fact. "
"Do NOT use stems like \"Which approach is more appropriate\", \"Which is best\", "
"or \"Which of the following would be most...\" — never ask the learner to "
"judge, rank, or justify.\n\n"
```

The difference matters in practice: the normal medium prompt's "scenario or
compare" instruction lets the LLM drift into judgment phrasing ("which
approach is best") that the classifier reads as `evaluate` → `hard`, not
`medium`. The strict variant explicitly bans exactly that phrasing and
mandates a "predict what happens" stem instead — this was tuned against an
observed drift pattern where medium-intent questions kept classifying hard.

---

## API endpoints

**File:** `urls.py:14-24`

| Method | URL pattern | View (`views.py`) | Returns |
|---|---|---|---|
| POST | `/api/generation/materials/<material_id>/start/` | `StartGenerationView` (172–235) | `{run_id, node_id}`, 201. Starts a full-material run. |
| POST | `/api/generation/materials/<material_id>/nodes/<node_id>/start/` | `StartGenerationView` (172–235) | Same, scoped to one `LearningObject`. |
| GET | `/api/generation/materials/<material_id>/questions/` | `MaterialQuestionsView` (238–280) | Per-node list of stored questions **including `correct_answer`** — teacher review only, never exposed to learners this way. |
| PATCH | `/api/generation/questions/<question_id>/` | `QuestionDetailView.patch` (291–350) | Updated question; validates `correct_answer` is a real choice letter (MCQ) or `True`/`False` (TF); marks cached audio stale. |
| DELETE | `/api/generation/questions/<question_id>/` | `QuestionDetailView.delete` (352–363) | 204; cascades to that question's `LearnerResponse` rows. |
| GET | `/api/generation/runs/?material_id=<id>` | `GenerationRunsView` (366–385) | Last 20 runs, newest first. |
| GET | `/api/generation/runs/<run_id>/events/?after=<seq>` | `GenerationTraceView` (388–428) | Run status + `GenerationEvent` rows with `seq > after` — the live-trace poll endpoint. |
| GET | `/api/questions/?node_id=&difficulty=&learner_id=` | `GetQuestionView` (15–51) | One unanswered `GeneratedQuestion` at the requested difficulty for that learner — **no `correct_answer`** (see [`QuestionSerializer`](#f-field-level-notes)). Learner-facing, downstream of this doc's scope. |
| POST | `/api/questions/submit/` | `SubmitAnswerView` (54–91) | `{is_correct, correct_answer, explanation}` — `correct_answer` is only ever sent *after* the learner has committed an answer. |
| GET | `/api/questions/stats/?node_id=&learner_id=` | `QuestionStatsView` (94–131) | Per-difficulty total/answered/correct counts for one learner+node. |

---

## Database

### a. Engine

**File:** `config/settings.py:68-76`

```python
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        # question generation writes trace events from a background thread
        # while the frontend polls — wait out transient SQLite locks
        "OPTIONS": {"timeout": 20},
    }
}
```

SQLite, single file at `backend/db.sqlite3`. The 20s `timeout` exists
specifically because this app writes `GenerationEvent` rows from a
background thread (Step 17) while the frontend concurrently polls the same
table — without it, SQLite's writer-lock would raise `database is locked`
under that access pattern.

### b. Models

#### `GeneratedQuestion` (`models.py:6-64`)

```python
node = models.ForeignKey(LearningObject, related_name="generated_questions", on_delete=models.CASCADE)

question_text = models.TextField()
question_format = models.CharField(max_length=3, choices=FORMAT_CHOICES)      # "MCQ" | "TF"
choices = models.JSONField(null=True, blank=True)
correct_answer = models.CharField(max_length=255)
explanation = models.TextField(blank=True, default="")

bloom_level = models.CharField(max_length=20, choices=BLOOM_CHOICES, db_index=True)
difficulty = models.CharField(max_length=10, choices=DIFFICULTY_CHOICES, db_index=True)
category = models.CharField(max_length=30, choices=CATEGORY_CHOICES)

intended_difficulty = models.CharField(max_length=10, choices=DIFFICULTY_CHOICES)
difficulty_match = models.BooleanField(default=True)
created_at = models.DateTimeField(auto_now_add=True)

class Meta:
    indexes = [models.Index(fields=["node", "difficulty"])]
```

- **Indexed:** `bloom_level`, `difficulty` (single-column `db_index=True`),
  plus a composite `(node, difficulty)` index. The composite index backs
  the actual runtime query pattern — `GetQuestionView`
  (`views.py:37-43`) and `QuestionStatsView` (`views.py:110,118`) both
  filter `GeneratedQuestion` by `node_id` + `difficulty` together.
- **Queried at runtime (serving):** `node_id`, `difficulty`, `id` (for the
  answered-exclusion list). `bloom_level` is indexed but not filtered on by
  any current serving view — the index exists for analysis/reporting
  queries, not the live quiz path.
- **Audit/research only, never read by serving code:** `intended_difficulty`,
  `difficulty_match`, `category`, `created_at`. See
  [field-level notes](#f-field-level-notes) below.

#### `LearnerResponse` (`models.py:67-77`)

```python
learner_id = models.CharField(max_length=50, db_index=True)
question = models.ForeignKey(GeneratedQuestion, on_delete=models.CASCADE)
selected_answer = models.CharField(max_length=255)
is_correct = models.BooleanField()
answered_at = models.DateTimeField(auto_now_add=True)

class Meta:
    indexes = [models.Index(fields=["learner_id", "question"])]
```

Queried at runtime by `GetQuestionView` (excludes already-answered
questions, `views.py:32-35`) and `QuestionStatsView` (`views.py:111-114`) —
both filter on `learner_id` + `question`, which the composite index backs.

#### `GenerationRun` (`models.py:80-109`)

```python
material = models.ForeignKey(LearningMaterial, related_name="question_generation_runs", on_delete=models.CASCADE)
node = models.ForeignKey(LearningObject, null=True, blank=True, related_name="question_generation_runs", on_delete=models.SET_NULL)
status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="running")   # running | finished | failed
started_at = models.DateTimeField(auto_now_add=True)
finished_at = models.DateTimeField(null=True, blank=True)
```

`node` uses `SET_NULL`, not `CASCADE` — deleting a `LearningObject` should
not delete the historical record that a run happened, only detach it from
the (now-gone) node. Queried by `StartGenerationView` (single-flight check
on `status="running"`, `views.py:213`), `GenerationRunsView`, and
`GenerationTraceView`.

#### `GenerationEvent` (`models.py:112-123`)

```python
run = models.ForeignKey(GenerationRun, on_delete=models.CASCADE, related_name="events")
seq = models.PositiveIntegerField()
event_type = models.CharField(max_length=30)
message = models.TextField(blank=True, default="")
data = models.JSONField(null=True, blank=True)
created_at = models.DateTimeField(auto_now_add=True)

class Meta:
    ordering = ["seq"]
    indexes = [models.Index(fields=["run", "seq"])]
```

`(run, seq)` index backs `GenerationTraceView`'s poll query
(`run.events.filter(seq__gt=after)`, `views.py:406`) — the entire live-trace
UI is one indexed range query per poll.

#### Upstream models referenced (not owned by this app — `lessons/models.py`)

```python
# lessons/models.py:59-94 — LearningMaterial
course = models.ForeignKey(CourseGroup, related_name="materials", on_delete=models.CASCADE)
outline_node = models.ForeignKey(OutlineNode, null=True, blank=True, related_name="materials", on_delete=models.CASCADE)
module_node = models.ForeignKey(OutlineNode, null=True, blank=True, related_name="module_materials", on_delete=models.CASCADE)
title = models.CharField(max_length=255)
pdf_file = models.FileField(upload_to="learning_materials/")
extracted_text = models.TextField(blank=True)
generated_json = models.JSONField(default=dict, blank=True)
status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROCESSING)   # processing | completed | failed
error_message = models.TextField(blank=True)
created_at = models.DateTimeField(auto_now_add=True)
```

```python
# lessons/models.py:113-127 — LearningObject
material = models.ForeignKey(LearningMaterial, related_name="learning_objects", on_delete=models.CASCADE)
kind = models.CharField(max_length=20, choices=Kind.choices)   # text | image
title = models.CharField(max_length=255)
content = models.TextField()
image_prompt = models.TextField(blank=True)
order = models.PositiveIntegerField(default=0)
```

This app only ever reads `kind`, `content`, `title`, `order`, `id`, and
`material_id` off `LearningObject` (Step 3) — never writes to it.

### c. Relationship diagram

```
LearningMaterial (upstream, lessons app)
    │ 1
    ▼ M
LearningObject (upstream, lessons app)
    │ 1
    ├──────────────────────────────┐
    ▼ M                            ▼ M (node, SET_NULL)
GeneratedQuestion              GenerationRun
    │ 1                            │ 1
    ▼ M                            ▼ M
LearnerResponse                GenerationEvent


LearningMaterial
    │ 1
    ▼ M  (material, CASCADE)
GenerationRun
```

`GenerationRun` has two parents: `material` (`CASCADE` — a run cannot
outlive its material) and `node` (`SET_NULL` — a run's history outlives a
deleted node). `GenerationEvent` belongs only to `GenerationRun`
(`CASCADE`).

### d. Cascade behavior

| Trigger | What happens | Where |
|---|---|---|
| Node regenerated (teacher re-runs generation) | `GeneratedQuestion.objects.filter(node=node).delete()` runs first, inside the same `transaction.atomic()` as the `bulk_create` of the new set | `services/pipeline.py:457` (Step 16) |
| ↳ cascades to | Every `LearnerResponse` row pointing at any of that node's deleted questions is deleted too (`on_delete=CASCADE`, `models.py:69`) | automatic, DB-level |
| `LearningObject` deleted | All its `GeneratedQuestion` rows deleted (`on_delete=CASCADE`, `models.py:38`) | automatic, DB-level |
| ↳ cascades to | All `LearnerResponse` rows on those questions deleted too (chained cascade) | automatic, DB-level |
| `GeneratedQuestion` deleted directly (teacher edit UI) | That question's `LearnerResponse` rows deleted | `QuestionDetailView.delete`, `views.py:352-363` |

**Why cascade delete, not soft delete:** this is a deliberate storage-policy
decision, not an oversight. Only the final curated set per node is ever
stored — regenerating a node is expected to reset that node's learner
history, since the old questions no longer exist to have been answered.
Versioning/soft-delete was considered and explicitly declined: it would
mean a learner's stats could reference a question bank that no longer
matches what's being served, which is a worse inconsistency than losing
history on regeneration.

### e. Migration state

**Files:** `migrations/0001_initial.py`, `migrations/0002_generationrun_node.py`

```
$ python manage.py showmigrations question_generation
question_generation
 [X] 0001_initial
 [X] 0002_generationrun_node
```

Both applied. `0001_initial` creates all four models; `0002_generationrun_node`
adds the `node` FK to `GenerationRun` (single-node run scoping).

### f. Field-level notes

**`difficulty` vs. `intended_difficulty` vs. `difficulty_match`**
(`models.py:50,54-55`):

- `difficulty` — the classifier's label (Step 9–10). **This is the only one
  the serving API filters on** (`GetQuestionView`, `QuestionStatsView`).
  What a learner is actually served is determined entirely by this field.
- `intended_difficulty` — what the prompt asked for (Step 11). Never read
  by serving code. Exists so the thesis can report how often the LLM's
  intent matched the classifier's judgment.
- `difficulty_match` — `intended_difficulty == difficulty`, computed once in
  `_classify_and_tag()` (Step 11) and never recomputed or overwritten at
  save time (`save_node_questions()`'s `_log_difficulty_mismatch()`,
  `services/pipeline.py:421-437`, only *logs* a mismatch — it does not
  correct these fields). Audit-only.

**`bloom_level` vs. `difficulty` vs. `category`** — three granularities of
the same classification, not three independent facts:

```
bloom_level:  remember | understand | apply | analyze | evaluate | create
                  │         │           │        │         │        │
difficulty:      easy ─────┘        medium ──────┘       hard ──────┘
                  │                    │                    │
category:  Facts and Information    Skills               Outcome
              │  Meaning
              └──┘ (understand → Meaning, not Facts and Information)
```

`bloom_level` is the classifier's raw 6-level output; `difficulty` and
`category` are both deterministic many-to-one collapses of it via the two
dicts in Step 10 — `category` groups `apply`+`analyze` into `Skills` and
`evaluate`+`create` into `Outcome`, while `understand` gets its own
category (`Meaning`) despite sharing a difficulty tier with `remember`.

**`choices`** (`models.py:44`) — `JSONField(null=True, blank=True)`:
- MCQ: `{"A": "...", "B": "...", "C": "...", "D": "..."}`
- TF: `null` (the statement itself, in `question_text`, is the "choice")

**Why `correct_answer` is excluded from the GET serializer but included in
the POST response:**
- `QuestionSerializer` (`serializers.py:6-18`) is used by `GetQuestionView`
  (the endpoint that hands a learner an unanswered question) and
  deliberately omits `correct_answer` — sending it would let a learner read
  the answer out of the network response before answering.
- `SubmitAnswerView` (`views.py:87-91`) returns `correct_answer` and
  `explanation` directly in its response body, but only *after* the
  learner's answer has already been recorded via
  `LearnerResponse.objects.create(...)` (`views.py:80-85`) — by that point
  revealing it can no longer affect the recorded response.
- `MaterialQuestionsView` (teacher-facing review, `views.py:238-280`) always
  includes `correct_answer` — its docstring explicitly flags "never expose
  this to learners" since it's a different audience.

---

## Flow diagram

```
                         POST /api/generation/materials/<id>/start/
                                        │
                                        ▼
                         StartGenerationView.post()  (Step 1)
                         creates GenerationRun, spawns thread
                                        │
                                        ▼
                         _run_pipeline()  (Step 2)
                                        │
                                        ▼
                 generate_questions_for_material()  (Step 3)
                 loads text LearningObjects from DB
                                        │
                    ┌───────────────────┴───────────────────┐
                    │         for each node (Step 4)          │
                    ▼                                          │
     ┌── generate_questions_for_node() ──────────────────┐    │
     │                                                     │    │
     │  PASS 1 (strict=False): for each difficulty×format  │    │
     │  ┌─────────────────────────────────────────────┐  │    │
     │  │ build prompt (5) → call Ollama (6) →         │  │    │
     │  │ parse JSON (7) → validate (8) →              │  │    │
     │  │ classify() (9) → map bloom→diff/cat (10) →   │  │    │
     │  │ tag intended vs classified (11)              │  │    │
     │  └─────────────────────┬───────────────────────┘  │    │
     │                        ▼                            │    │
     │              ┌── bloom_level == "create"? ──┐        │    │
     │              │YES                    NO      │        │    │
     │              ▼                        ▼       │        │    │
     │        drop (12), emit          keep in       │        │    │
     │        question_dropped         all_questions │        │    │
     │              │                        │        │        │    │
     │              └────────────┬───────────┘        │        │    │
     │                           ▼                     │        │    │
     │              check shortfall (13)               │        │    │
     │                           │                      │        │    │
     │              ┌── any difficulty short? ──┐       │        │    │
     │              │YES                   NO    │       │        │    │
     │              ▼                       │     │       │        │    │
     │   PASS 2 (14): regenerate short      │     │       │        │    │
     │   levels, strict=True, up to         │     │       │        │    │
     │   MAX_REBALANCE_ROUNDS (loops back    │     │       │        │    │
     │   into the same build→call→parse→    │     │       │        │    │
     │   validate→classify→tag→filter       │     │       │        │    │
     │   chain above)                        │     │       │        │    │
     │              │                        │     │       │        │    │
     │              └───────────┬────────────┘     │       │        │    │
     │                          ▼                          │        │    │
     │            _select_final_questions() (15)           │        │    │
     │            trim to QUESTION_DISTRIBUTION             │        │    │
     └──────────────────────────┬──────────────────────────┘    │
                                 ▼                                  │
                    save_node_questions() (16)                     │
                    transaction.atomic():                          │
                      delete old GeneratedQuestion rows             │
                      (cascades to LearnerResponse)                 │
                      bulk_create new rows                          │
                                 │                                  │
                    emit node_finished, loop back ──────────────────┘
                                 │
                    (all nodes done)
                                 ▼
                    emit material_finished (17)
                    GenerationRun.status = "finished" (18)
                                 │
                                 ▼
              GET /api/generation/runs/<id>/events/?after=<seq>
              polled by frontend throughout, not just at the end
```
