# Teacher content publishing milestone

Scope: PDF extraction, equivalent-object grouping, Normal/Simplified/Elaborated
preparation, teacher corrections, and publication with audio. Student accounts,
enrollment, adaptive progression, and student learning outcomes are separate work.

## Acceptance checks

1. Upload an outline and several lesson PDFs. Verify extracted sections, complete
   examples/lists, image descriptions, source references, and duplicate handling.
2. Confirm the lesson objects. Distinguish failed semantic evaluation from a
   genuine no-match result; errors should be visible in Review Connections.
3. Generate versions. A standalone object's old generated versions must not
   determine the Normal baseline when a second source joins. Saved teacher/source
   decisions remain stable; changing those requires an explicit teacher decision.
4. Select two sources consecutively as Simplified. The second becomes active;
   the first is retained under Other source versions. Reassign it again and verify
   both source texts survive. Extra is omitted from active version audio synthesis.
5. Edit generated wording. Its old audio URL must clear. Changed Normal text must
   flag generated versions whose source fingerprint is stale for review.
6. Simulate generation and audio errors. The run must fail and the topic must not
   be marked published. Retry after resolving the error. Audio for unchanged text
   and configured voice/provider should be reused.
7. Listen to Normal, Simplified, and Elaborated audio from the backend media files.
   Verify text/audio correspondence on the actual deployment machine.

## Scientific evaluation still required

Code tests do not establish grouping accuracy, factual accuracy, reading-level
suitability, or reduced teacher workload. No such results are claimed here.

- Export real pairs with `python manage.py export_grouping_pairs --output pairs.jsonl`.
  Have subject reviewers independently label interchangeability, not just topic
  similarity. Include definitions versus examples, contradictions, partial
  overlap, unrelated passages, and paraphrases. Resolve disagreements.
- Separate development and test data by source documents or topics. Tune only on
  development data; report false merges, missed matches, precision, recall, and
  number of automatically grouped test pairs. Evaluate full groups as well as pairs.
- Use `python manage.py evaluate_grouping --pairs labeled.jsonl --output evaluation.json`
  with the exported schema and required labels/splits. Keep the test set unchanged.
- Review generated versions for preserved essential facts, unsupported additions,
  clarity, and useful explanation. Elaborated means more explanation; it need not
  have harder vocabulary. Current readability heuristics are conservative proxies,
  not a factual validator or a validated reading-level instrument for your students.
- Measure teacher task completion time and number of corrections against the
  existing preparation process, including failed or repeated runs.

## Known boundaries

The generator checks output structure, nonempty/distinct wording, and length.
These do not prove factual grounding. Teacher content review remains necessary.
Gemma's reported confidence is not a calibrated probability. Operating thresholds
must not be described as measured accuracy.

This update retains the current readability heuristic pending evaluation instead
of introducing an unvalidated replacement. Unsupported model input lengths are
reported, not automatically split into potentially incomplete ideas.

Publishing still uses an in-process background thread. A server restart interrupts
that work; durable job recovery and a versioned publication snapshot remain future
deployment work. A failed republish marks the current topic unpublished because
there is no immutable previous publication snapshot to serve safely.
