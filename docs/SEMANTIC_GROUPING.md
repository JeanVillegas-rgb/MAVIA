# Semantic learning-object grouping

## Current rollout

### Teacher workflow

Strong matches use the configured automatic policy. Uncertain suggestions appear
in a collapsed, optional **Suggested connections** panel above the concept list.
Teachers can continue to questions and publish with unresolved object suggestions;
continuing does not accept or reject those suggestions or change group membership.

Opening the panel displays one pair side by side (stacked on narrow screens),
with **Connect**, **Keep separate**, and **Later**. Wording highlights identify
words absent from the other passage; they do not identify semantic errors. They
can be switched off. Later defers the pair in the current browser session and
does not train the model or record a rejection. **Show later suggestions** restores
the deferred list. Edited passage content becomes eligible for review again.

Semantic refresh only moves standalone objects; members of existing multi-object
groups stay together. New objects may still join a group after every target member
passes the policy. The **Separate** action records rejection before updating
snapshots and does not run model inference. Subsequent matching respects that
rejection. These protections apply to the existing object records; regeneration
that replaces records is a separate workflow.

The semantic matcher is the repository default. It uses the configured MAVIA
teacher-workflow cutoffs: `0.60` for automatic grouping and `0.30` for teacher
review. These are operating thresholds, not calibrated probabilities or
percentages of identical words. Evidence identifies configured thresholds as
unvalidated until a teacher-labeled evaluation replaces them.

After validation, the routing is:

- High: automatically group, provided every candidate-group member passes and
  the winning group has a sufficient lead over alternatives.
- Medium: create a suggestion for the existing teacher review screen.
- Low: do not create a suggestion; retain the standalone learning object.

Only confirmed learning objects in the same topic, of the same kind, from
different materials are eligible. Titles are never model input and never group
objects by themselves. In automatic mode, however, an exact normalized,
non-generic, one-to-one label may corroborate a pair whose content already clears
the existing review threshold. This promotes the pair directly to automatic
grouping without creating additional teacher-review work. It handles equivalent
paragraph and glossary material whose different writing lengths depress the
content-only score. Every destination-group member must still clear the content
threshold, represented objects are excluded, teacher rejections still win, and
ambiguous labels or destination groups retain the ordinary content-only behavior.

Set `SEMANTIC_GROUPING_LABEL_CORROBORATION=False` to disable this corroboration
and restore content-only behavior. Review mode remains strictly non-automatic;
the feature only promotes pairs while `SEMANTIC_GROUPING_MODE=auto`. Structural
and generic labels such as `Lesson 1`, `Examples`, `Summary`, `Figure 1`, and
`Table 2` are excluded. There are no hardcoded definition/example content
categories. A model can still confuse related content with interchangeable
content; teacher evaluation must specifically test that case.

## Pipeline

1. Encode content with the pinned `all-MiniLM-L6-v2` sentence transformer.
2. Shortlist up to ten groups by embedding cosine similarity.
3. Re-score the source against every member of each shortlisted group with the
   pinned `stsb-roberta-base` cross-encoder, in both directions.
4. Use the lowest pair score and lowest group-member score. A strong match to one
   member must not hide a weak match to another.
5. Apply the configured or teacher-validated thresholds and winner-margin check.
6. If the ordinary result is not already automatic, optionally corroborate an
   exact specific label by explicitly scoring every member of its single,
   unambiguous destination group against the existing review threshold.

Embeddings and pair scores are cached by content hash and model version in
`backend/semantic_cache/`. This derived cache is separate from the application
database and excluded from Git. First-time CPU inference is slower than cached
comparisons. Models are loaded once per process, so a server restart adds warmup
time. When public weights are missing, the default development configuration
downloads them once; learning-object content is not sent during that download.
For an offline deployment, set `SEMANTIC_GROUPING_ALLOW_MODEL_DOWNLOAD=False`
and prepare the cache before starting the server.

Empty or over-length content is not silently truncated or automatically grouped.
The current implementation skips unsupported sources/pairs; it does not create a
special review card for them. The offline evaluation counts unsupported pairs as
requiring manual inspection. Image-only objects have no semantic image matching.

## Preparation (PowerShell, from backend)

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py prepare_semantic_grouping --download
```

Preparation downloads pinned model weights into the Hugging Face cache and
precomputes supported content embeddings. It does not change groups. Explicit
preparation is optional in development, but it avoids making the first upload
wait while missing model weights are downloaded.
The cross-encoder is a substantial download. For an Xet transfer issue, retry in
a new shell with `$env:HF_HUB_DISABLE_XET='1'` before running preparation.

## Teacher validation

Export candidate examples without treating existing groups as ground truth:

```powershell
.\.venv\Scripts\python.exe manage.py export_grouping_pairs --output semantic_evaluation/teacher_pairs.jsonl --limit 200
```

Output paths must be new: commands refuse to overwrite existing work. The export
contains learning content; keep it private. Sampling includes shared-title pairs,
different-title pairs and cross-topic negatives; titles are only used for sampling.

Each JSONL row needs these human-entered fields:

- `label`: `equivalent` (same instructional content, usable as variants), `related`
  (same topic but different content), or `unrelated`.
- `reviewed_by`: the reviewer identifier.
- `split`: `development` or `test`.
- `notes`: optional explanation of difficult cases.

Use independent materials/topics for the two splits. Do not put the same object
or duplicated content in both. A random pair split can leak the same content into
both sets. Remove duplicate/reversed pairs and pairs crossing the chosen split
boundary before evaluation. Both splits need equivalent and non-equivalent cases.
The exported 200 pairs are a starting pool, not necessarily a sufficient final
validation set.

```powershell
.\.venv\Scripts\python.exe manage.py evaluate_grouping --pairs semantic_evaluation/teacher_pairs.jsonl --output semantic_evaluation/calibration.json
.\.venv\Scripts\python.exe manage.py audit_semantic_grouping --output semantic_evaluation/group_audit.json --limit 50
```

Evaluation selects thresholds using development data only, then reports held-out
precision, false automatic matches, recall, review load and a precision confidence
bound. It also reports the lexical baseline's pair-level automatic precision.
It refuses unlabeled data and cross-split content leakage. The audit is read-only;
inspect actual candidate groups, retrieval misses and definition-versus-example
errors. Pair metrics alone do not validate retrieval or whole-group behavior.

## Validate and tune

Set `SEMANTIC_GROUPING_MODE=review` when collecting labels without allowing
automatic grouping. Its default review cutoff of `0.30` is provisional, not a
validated accuracy claim.

For automatic mode, first inspect the evaluation and group audit. The generated
artifact deliberately has `approved_for_auto: false`. A responsible reviewer may
approve it only after checking the evidence. Runtime also requires at least 40
held-out automatic predictions, zero observed false automatic matches, at least
20 negative test pairs, the matching model fingerprint and the dataset hash.
These are minimum rollout gates, not statistical proof of accuracy. More diverse
examples and a stronger precision bound may be necessary.

Set `SEMANTIC_GROUPING_CALIBRATION` to that reviewed artifact's absolute path and
restart to replace the configured defaults with teacher-validated thresholds.
This remains the recommended production route.

Before calibration, the repository runs in `auto` mode with cross-encoder `0.60`,
review `0.30`, SBERT `0.60`, and winner margin `0.05`. Explicit environment
overrides are recorded as `environment_unvalidated` in diagnostic evidence. A
high decision must also clear the SBERT gate, winner margin, and every group-member
check. These values must be re-evaluated as teachers accept and reject more pairs.
An environment change does not rebuild existing groups automatically.
Previously incorrect groups need their own teacher cleanup; this feature does not
silently split them.

Return to `legacy` to use the old matcher for future decisions. This does not undo
groups already changed. Diagnostic evidence includes model fingerprint, content
score, embedding score, checked-member count, thresholds, margin and elapsed time.

## Tests

```powershell
.\.venv\Scripts\python.exe manage.py test lessons.test_semantic_grouping --noinput
```

Routing and safety tests use fake model outputs and need no downloads. They test
implementation behavior, not real-model accuracy. Actual accuracy requires the
labeled evaluation above.
