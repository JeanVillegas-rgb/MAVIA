# MAVIA — shared agent log

A running record of what each coding agent changed, and the place where agents
leave notes for each other. The user switches between agents (Claude Code and
Codex), so this file is the handover.

Read `docs/PROJECT_CONTEXT.md` first for what the project is and how it works.
This file is only *what happened and what is pending*.

## How to use this file

- **Append at the bottom.** Newest entry last. Never rewrite or delete someone
  else's entry; if they got something wrong, say so in your own entry.
- **Log a session, not every command.** One entry per working session, written
  when you finish or when you hand back.
- **Record what would surprise the next agent**: decisions you made on the
  user's behalf, things you deliberately did not do, and anything you changed
  in the live database.
- **Open questions go in `## Open threads`** near the bottom, and are removed by
  whoever resolves them (noting the resolution in their entry).
- Be honest about what you did not verify. "Tests pass" and "I reasoned it
  through" are different claims.

### Entry template

```markdown
### YYYY-MM-DD — <agent> — <one-line summary>

**Branch / commits:** jean-latest, <sha>..<sha>
**Tests:** <what you ran, and the result>
**Changed:** <files or areas, and why>
**Live database:** <anything you changed, or "untouched">
**Decisions I made:** <choices the user did not explicitly approve, and the cost if wrong>
**Not done / watch out:** <what you left, and what could bite>
```

---

## Log

### 2026-09-21 — Claude Code — concept bundles land; run-through fixes; docs for handover

**Branch / commits:** `jean-latest`, `f7596d7..c769e3e` (57 commits over several
sessions). Merge-era code preserved on `current-with-merge-function` (pushed).

**Tests:** `python manage.py test -v 1` → 796 passing. Gold paths unchanged:
topic 62 10/10, topic 79 4/8 with the four recorded `known_missing` gaps, no
forbidden edges, both orders matching. `npm run build` clean.

**Changed (six pieces of work):**

1. **Grouping merges + revised learning-path criteria** (spec
   `2026-09-17-grouping-merge-and-learning-path-design.md`). RefD key-term
   reference replaced name-to-text similarity; Examples concepts excluded and
   ordered last; numbered labels normalised; head-word mentions and section
   containment added; cross-section cap removed; uploaded PDFs now deleted with
   their rows (436 orphans → 0). Gold went 0/10 and 1/8 → 10/10 and 4/8.
2. **Concept bundles** (spec `2026-09-20-concept-bundles-design.md`, plan
   `2026-09-20-concept-bundles.md`). The manual merge step is gone; a concept
   holds an ordered bundle per PDF, formed automatically when another PDF
   corroborates it, with a card for uncertain cases. Version roles moved onto
   the group; generated versions are written per object; the review screen
   shows one block per PDF with Move out / Move to… / ↑ / ↓.
3. **Fixes from the user's real run-through (same day):** single-object bundles
   keep their own title (three concepts were all being named "Matter");
   overlapping row controls; images need label corroboration before grouping
   automatically; the figure-description prompt rewritten as ROLE / TASK /
   CONTEXT / FORMAT with a no-preamble rule, 2–4 sentences (6 max), and an
   instruction not to restate the lesson text it is given as context.

**Live database:** backed up to `db.sqlite3.pre-repair.20260921`, then:
12 concept labels corrected in place by a new `repair_bundle_labels` command;
topic 152's over-grouped figure concept split so each figure stands alone; two
`LearningObjectMatchSuggestion` rows deleted because a repair script had
recorded its own splits as *teacher* decisions. Figure descriptions were **not**
regenerated. Neither topic is published.

**Decisions I made:**

- Kept the three-criterion vote and its paper lineage rather than replacing the
  approach, because the manuscript commits to it. Cost if wrong: the criteria
  remain weaker on process-type lessons than a different method might be.
- Stopped calibrating after two runs at 14/18 required edges and recorded the
  four gaps in the fixtures instead of tuning them away. Cost if wrong: the
  adaptive engine has no remediation link for those four.
- A rejected suggestion, a locked label and a teacher-provenance role outrank
  every automatic path. Cost if wrong: a teacher has to redo a connection
  manually that the system could have made.
- Bundles are derived, not stored (no new table). Cost if wrong: every consumer
  must go through one helper, which is a convention, not a constraint.
- A concept takes its heading as a name only for a 2+ object bundle. Cost if
  wrong: a single-object concept with a poor title keeps it.

**Not done / watch out:**

- **Publishing is blocked by the environment**, not by code:
  `Edge TTS failed: Cannot connect to host speech.platform.bing.com`. The gate
  now refuses rather than shipping a silent lesson.
- **31 empty `LearningObjectGroup` rows** remain from deleted materials. The
  delete endpoint never calls `remove_empty_learning_object_groups`. This
  matters because a stale group carries `version_selection`, so a re-upload can
  inherit a role or a locked name for text that no longer exists. Fix proposed,
  awaiting the user.
- **Two consumers broke silently** when PDF-supplied `LessonVariant` rows were
  retired: the publish gate and audio generation. Both fixed, both invisible to
  a green suite. If you change version storage, check `topic_publish.py` and
  `audio_generator.py` by hand.
- The chatter strip in `image_describer.py` deliberately **under-reaches**:
  some chatter survives rather than risk deleting a real sentence. Keep that
  direction if you touch it.
- No frontend test runner, so the bundle controls have no automated coverage.

### 2026-09-21 — Claude Code — a read stops deciding roles; Gemma's broken JSON recovered; TTS retries a lossy link; whole bundles reach the path and the review screens

**Branch / commits:** `jean-latest`, uncommitted on top of `2d3eacd`. The user
asked for the log entry only, so nothing is staged or committed.

**Tests:** `python manage.py test -v 1` → **827 passing** (800 before, plus 27
new). Gold paths re-run on their own: topic 62 and topic 79 both `ok`, so
10/10 and 4/8 with the recorded `known_missing` gaps, no forbidden edges, both
orders matching. `npm run build` clean.

**Changed (three pieces of work):**

1. **Step 1 no longer classifies.** The user noticed a concept already showing
   Simplified/Elaborated in the grouping step. It was real: the review payload
   calls `assign_group_versions` on every load, and that call *wrote* a
   readability-derived role (provenance `heuristic`) into `version_selection`
   before Gemma or the teacher had ruled on anything. Now only a run that was
   asked to classify writes a role; a read still returns the proposal so a
   caller could show it as a suggestion, but records nothing. The role badge
   and its `bundleRoleLabel` helper are gone from the step-1 concept cards,
   and the dead CSS from both mirrored stylesheets.

   **One regression this would have caused, caught by a test and fixed:** when
   a teacher moves a bundle into a slot another bundle holds, the displaced one
   is not meant to become an Extra — it takes the primary slot its wording
   actually fits (decided 2026-09-20). That re-derivation was being *persisted
   by the next page load*. With reads inert it would have stayed EXTRA and its
   wording would have been regenerated instead of used. The re-derivation now
   happens in `assign_source_to_slot`, with the decision that causes it.

2. **Gemma's unparseable reply is recovered.** Publishing topic 152 failed on
   "Comparing the Three States" with "Gemma did not return valid JSON", three
   attempts, every time. Reproduced 3/3 with an identical mechanism: both
   variants come back correct and inside their word limits, but the model
   closes the `elaborated` string with a typographic right quote (U+201D)
   instead of `"`. The string never terminates, the constrained decoding never
   sees the object close, generation runs to `num_predict` (`done_reason:
   length`) and the tail fills with the prompt's own word limits echoed back
   ("52 words. 48 words. 27 words." repeating). `_parse_response` now repairs
   the *delimiters* and keeps the result only when it then parses; the model's
   own wording, including a curly apostrophe mid-sentence, is untouched. A
   reply with genuine curly quotes parses first time and never reaches the
   repair. Verified against the live model on the real object: recovered on
   the first attempt, 17 and 25 words against limits of 27 and 48.

3. **Edge TTS retries a dropped connection.** The host was never blocked. A
   10-shot probe put the TLS handshake at **2 successes in 10** on the user's
   link, with the other 8 reset immediately (WinError 10054). Because
   `_synthesize_text_to_mp3_with_edge` made a single attempt and raised, one
   dropped clip aborted the publish for every clip after it -- which is why
   the same run kept failing while the media directory filled up (293 files in
   `audio_versions`, 21 in `audio_lessons`; material 28's playlist was already
   10/10). Each clip is now attempted up to `EDGE_TTS_ATTEMPTS` times (default
   20, `EDGE_TTS_RETRY_DELAY` 1.5s between), and a partial file from a dropped
   stream is deleted before the retry so a truncated clip cannot be mistaken
   for a finished one. Verified against the live link: 3 of 3 clips
   synthesized, 5-9s each, 4 retries consumed across them.

4. **The published learning path served only a concept's bundle lead.**
   Found by answering a question from the user -- "are the bundled learning
   objects really included in the literal learning path?" They are not, and it
   was two bugs in `learning_path/services/published.py::_versions`:

   - Normal was read as `representative.content`, which is the bundle's *lead*
     and drops every object after it. On the real topic, the concept
     "Comparing the Three States" served **77 characters of 326** -- object
     298 alone -- while Volume, Particle arrangement and Flow appeared nowhere
     in the payload.
   - Simplified and Elaborated were read from `LessonVariant` rows only, and a
     version another PDF *supplies* has no such row by design. So that PDF's
     wording never reached the path at all; a generated variant was served
     instead, including one generated before the teacher connected the two
     bundles and never invalidated by the regroup.

   **15 of 22 concepts across the two live topics were affected -- all 10 of
   topic 169.** The lesson package (`course/services._build_chunk`) had it
   right all along, so the two readers disagreed: for the same concept the
   package served 326 / 698 / 503 characters where the path served 77 / 66 /
   105.

   `_versions` now reads through `normal_bundle_for` and `version_bundles`,
   and imports `_generated_versions` / `_version_from_segments` from
   `course.services` rather than copying them -- the rule that a generated
   version short of its bundle is reported missing instead of half-served is
   safety-critical and must not exist in two places. Each version also now
   carries `segments` alongside `text` and `audio_url`: a four-object version
   has four clips, and a single `audio_url` is only the first of them, so a
   reader playing it alone would give a learner a quarter of the version with
   no way to tell. The two documented keys are unchanged, so existing readers
   are unaffected.

   Verified on the live database: **0 concepts short** on both topics, and
   objects 299, 300, 301, 316 and 317 -- previously reachable through no
   learner-facing path at all -- now appear.

5. **The final-review screen described a concept as "lead + leftovers".**
   The user could not trace whether the screen was behaving correctly, which
   is how this was found. It rendered the concept's representative object with
   its own text as "Normal", and every other member as "Other variation" --
   the pre-bundle model. Three consequences, all visible on topic 152:

   - Normal was the lead's text, so three of the four objects of "Comparing
     the Three States" were shown as variations of themselves.
   - A generated version showed only the lead's segment -- one of the four
     that had actually been written and were sitting in the database.
   - A version a PDF supplies was printed in full AND again object by object,
     so every word appeared twice with nothing saying they were the same.

   The payload now carries **Normal as a slot of its own** (it was absent
   entirely, which is why the screen fell back to the lead), gathers **every
   segment** of a generated version across the Normal bundle, refuses to offer
   a generated version **short of its bundle**, and tells each slot's `source`
   (`pdf`/`generated`), its material and the objects it is made of -- a
   generated segment carrying the wording that was *written*, not the object
   it was written from. The screen renders one block per role; every object
   appears exactly once, under the role it plays. The header counts versions
   and files instead of objects, so "6 variations" became "3 versions from
   2 files".

6. **Step 1's row controls were named after the data, not the decision.**
   "Move out" and "Move to..." both read as "move" while one makes a new
   concept and the other joins an existing one, and the reorder arrows sat
   beside them looking as though they changed concepts too. The four controls
   are now two labelled groups -- **Wrong concept?** (Give it its own concept
   / Move into another concept...) and **Order in this file** (up/down) -- and
   the panel's intro says plainly that the arrows never move an object between
   concepts. `aria-disabled` rather than `disabled`, the aria-labels naming the
   object and its file, and the focus-restore refs are all unchanged.

**Live database:** **untouched.** Every query was a read; the two model probes
went straight to Ollama and to edge-tts without going through the ORM. Checked
afterwards: **0 groups carry a `heuristic` role**, so no repair is needed for
roles written by past page loads.

**What I found in the live database (2026-09-21, after the user re-uploaded):**

- Topics 152 and 169, two PDFs each, 50 learning objects, 31 groups, **none
  empty** — the re-upload refilled the 31 empty ones from last session. The
  delete-endpoint bug behind them is still there; the stale-`version_selection`
  risk showed up for real, with group 326 inheriting its old `auto_label`.
- Topic 169: all 10 concepts paired across both PDFs, every role
  `llm_validated`. **0 concepts missing a version** — it is content-complete
  and only TTS blocks it.
- Topic 152: only Solid/Liquid/Gas are cross-PDF. **4 suggestion cards are
  pending the teacher** (ids 145–148), all heading-object/figure pairs that
  extraction split inside one PDF.
- `ConceptPrerequisite` and `LearningPathStep` are both 0: neither topic has
  ever been published, so the derived path has still never been compared with
  the gold standard.

**Decisions I made:**

- Scoped the role change to the *write*, not the computation: a read still
  returns the proposal. Cost if wrong: a caller could still surface a
  heuristic role as though it were decided.
- Put the displacement re-derivation in `assign_source_to_slot` rather than
  keeping any write on the read path. Cost if wrong: two teacher roles written
  straight to the record (not reachable through the endpoint) would no longer
  be resolved in storage, only in the returned value.
- Repaired the JSON delimiters rather than loosening the grounding checks or
  rewriting the prompt. The prompt is content generation, which is the
  groupmate's area, and the model's output was *correct* — only its envelope
  was malformed. Cost if wrong: a reply needing its own quotes rewritten is
  still refused, which is the deliberate under-reach direction.
- Changed three existing tests that built their state through a read
  (`test_a_pdf_supplied_version_stores_no_copied_text`,
  `test_a_displaced_automatic_role_keeps_its_own_provenance`,
  `test_two_teacher_roles_for_one_slot_are_settled_by_recency`) plus
  `BundleGenerationTests.setUp`. Each now classifies first, which is how
  production reaches that state, and each carries a dated comment saying why.

**Not done / watch out:**

- **`backend/course/variant_generator.py` is one of the two files the user
  keeps uncommitted work in.** It was clean in git when I edited it. The new
  tests went into a new file, `course/test_variant_parsing.py`, so
  `course/tests.py` still holds only the user's own changes. Nothing staged.
- **The Edge TTS host is lossy, not blocked — I got this wrong at first.**
  Earlier in this session I read the resets as a per-hostname block, because
  `bing.com`, `google.com` and `api.github.com` completed while
  `speech.platform.bing.com` and `huggingface.co` were reset. Repeating the
  probe 10 times showed 2 successes, so the host is reachable and the link
  just drops most handshakes. The retry above is the fix; the earlier
  network-switching advice was chasing the wrong thing.
- `AUDIO_TTS_PROVIDER=auto` falls back to Windows SAPI and was verified
  working here (95,710-byte wav), but **the user chose to keep the Edge voice
  and .mp3 output** rather than change the audio the manuscript describes. Do
  not switch the provider without asking them.
- The retry defaults (20 attempts, 1.5s apart) are sized for a link at roughly
  20% success. On a healthy connection the first attempt wins and nothing
  changes; on a dead one a clip now takes ~30s to give up instead of ~1s.
- The four pending suggestions on topic 152 were ruled on by the teacher
  after this entry was first written: 147, 148 and 145 accepted, 146 rejected.
  Required edges on topic 152 went 6/10 → 9/10 and 20 concepts became 12. One
  **forbidden** edge appeared with it (`Solid → Gas`); see
  `docs/learning_path_revision_2026-09-17.md` for the evidence and why a
  threshold will not fix it.
- **A published step is still titled after its bundle's lead**, not the
  concept. The step for "Comparing the Three States" is titled "Shape". I
  deliberately did not change it with the versions fix -- it is the same
  family of defect (the path speaking for a bundle through its lead) but a
  separate change, and `title` is part of the documented payload shape. The
  review screens no longer have this problem; only the published payload does.
- **The two "Changing From One State to Another" concepts are still split**
  (objects 318 and 319, both from the same PDF). No card was raised because
  the other PDF has no matching section, so they need **Move into another
  concept...** by hand. That is the last required edge missing from topic 152.
- The review screens now have **no automated coverage of their rendering** --
  the payload is tested, the JSX is not, because there is still no frontend
  test runner.
- **`course/services.py` now has two importers of its private helpers**
  (`_generated_versions`, `_version_from_segments`). That was the deliberate
  choice over duplicating the half-served rule, but if those helpers move,
  `learning_path/services/published.py` moves with them.

### 2026-09-22 — Claude Code — parallel concepts stop borrowing each other's words; a gold fixture that holds the pipeline's own grouping

**Branch / commits:** `jean-jure-latest`, uncommitted on top of `3820565`.
Nothing staged or committed.

**Tests:** `python manage.py test -v 1` → **845 passing** (837 before, plus 8).
`test_gold_paths` runs three lessons now and all three are `ok`. The new one was
confirmed **failing before the fix**, on
`forbidden_accepted: [["solid","gas"]]`, which is why it exists.

**Live database: untouched.** Every number below was re-derived read-only by
running `concepts_for_topic` + `criteria.decide_pairs` — which is exactly what
`publish_learning_path` does, so these *are* the numbers a republish would
store. I did not republish 152/163: the stored rows are stale, but re-deriving
answers the question without writing, and publishing is the teacher's action.

**What was wrong, and what I did about it**

The task was that edges are accepted on evidence that is a single ordinary
English word, with `Solid → Gas` (topic 152) and 20 cross-lesson edges
(topic 163) as the live consequences. The recorded candidate fix was to stop
non-technical and rendering vocabulary counting as distinctive.

1. **That candidate direction does not work, and I measured it rather than
   arguing it.** A filter built to the stated principle — words describing the
   medium or the prose rather than the science, written deliberately *not* to
   spare any particular edge — takes **gold topic 62 from 10/10 to 9/10**. It
   loses `comparing → changing`, which is carried by `["explain", "four",
   "outline"]` at `ref_forward` 0.0441: no name, no head word, no section
   containment. That is the *same evidence class* as the four words carrying
   `Solid → Gas`. Any vocabulary filter honest enough to catch "drawn" and
   "spaced" also catches "explain" and "outline". The acceptance test and the
   proposed fix are incompatible, so I did not ship a curated list that spares
   one edge — that is the lesson-specific word list the 2026-09-17 calibration
   decided against.

2. **What shipped is structural.** `contained_in` already reads section
   structure downward (a passage under "Matter" builds on Matter). The sideways
   reading is that two passages under **one** heading, neither of which is what
   that heading names, are **coordinate siblings** — Solid, Liquid and Gas under
   "Matter". Between two siblings an edge now needs a reference that *names* its
   target (the name, a head word, or section containment) rather than merely
   sharing vocabulary with it, because parallel passages share vocabulary by
   construction: the author describes each state the same way, which is what
   "drawn as evenly spaced dots" and "drawn as widely spaced dots" are.
   `criteria.presented_in_parallel` / `criteria.names_the_target`. **No constant
   moved.** This is not the removed sibling rule — that keyed on the words of
   the topic *title*, this keys on the documents' own headings.

   | | gold 62 | gold 79 | live 152 | live 169 | live 163 |
   |---|---|---|---|---|---|
   | Required accepted | 10/10 | 4/8 | 9/10 | 4/8 | — |
   | New gaps / gaps closed | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | — |
   | Forbidden | 0 | 0 | **0** (was 1) | 0 | — |
   | Order matches | yes | yes | yes | yes | — |
   | Accepted total | — | — | 19 (was 20) | 8 | 39 (was 41) |

3. **Topic 163 is a data-modelling problem, not a criteria problem, and now
   there is a number for it.** Re-derived with each of its three PDFs as its
   **own topic**: 41 accepted edges → **22, none crossing lessons**, and all 22
   are plausible. The coordinate-sibling rule prunes 2 of its edges and none of
   the 20 that cross lessons — concepts from different PDFs share no heading, so
   it could not. **Topic 163 should be three topics.** That is a teacher action
   in the UI; I did not do it.

4. **A third gold fixture, and the reason it is worth more than the fix.** The
   recorded blind spot was "the fixtures hold older text". That turned out to be
   only half of it. I first exported topic 152 through the existing
   `export_gold_concepts`, which writes the **teacher's** grouping with today's
   text: 7 concepts, 8/10 required, and **0 forbidden edges** — it cannot see
   `Solid → Gas` at all. The denominator is why: `REF_MAX_DF_RATIO` allows a
   term in at most `floor(n × 0.34)` concepts, the diagram vocabulary sits in 3
   concepts either way, so at n=7 the cap is 2 and the words are dropped, at
   n=14 the cap is 4 and they survive. **The teacher's ideal grouping hides the
   failure the teacher sees.**

   So the new fixture freezes the shape a publish actually derives.
   `export_live_concepts` (new command) writes `concepts_for_topic` output and
   labels each concept with the teacher's concept it belongs to.
   `gold.py` grew two things the older fixtures could not express: **several
   concepts may share a key** (the pipeline split what the teacher keeps whole —
   an edge between two such concepts is a grouping result, not a prerequisite
   claim, and is not scored), and **a concept may carry no key** (3 of topic
   152's 14; they still take part in the derivation because they change document
   frequencies and the order, but nothing is scored against them).

**Changed:**

- `learning_path/services/criteria.py` — `named_sections`,
  `presented_in_parallel`, `names_the_target`, and the veto in `decide_pairs`.
- `learning_path/services/gold.py` — shared/absent keys, order collapsed over
  them, `unkeyed_concepts` in the report. Behaviour on 62 and 79 is unchanged
  (every key there is unique and non-null).
- `learning_path/management/commands/export_live_concepts.py` — new.
- `learning_path/fixtures/gold_map_152.json`, `gold_topic_152.json` — new.
- `learning_path/test_gold_paths.py` — third lesson.
- `learning_path/test_criteria.py` — `ParallelPresentationTests` (7).
- `learning_path/CRITERIA.md`, `docs/learning_path_revision_2026-09-17.md`,
  `docs/PROJECT_CONTEXT.md` §4/§6/§7.

**Decisions I made:**

- **Abandoned the recorded candidate fix** instead of curating it into a list
  that keeps 62 at 10/10. Cost if wrong: the rendering vocabulary still counts
  as distinctive everywhere the two concepts are not siblings, so a lesson that
  puts its diagrams under different headings could still produce this.
- **Reconstructed the teacher's concept map for topic 152** from `gold_map_62`
  (same lesson) plus the merges the revision doc records the teacher applying.
  Five of the seven member counts match 62 exactly (matter 4, solid 4, liquid 5,
  gas 4, comparing 6); `changing` has 2 objects today against 3, and `examples`
  4 against 3, because extraction chunked differently. Cost if wrong: the
  fixture asserts a grouping the teacher did not actually specify. **Worth a
  teacher's eye before this is quoted in the manuscript.**
- **Recorded `comparing → changing` as topic 152's one `known_missing`** rather
  than treating it as a criteria failure. It is the same-name veto doing its job
  over two split concepts (318/319). When the teacher joins them the gap closes
  and the test will fail *on the gap closing* — which is the intended behaviour,
  and it needs `known_missing` emptied and the fixture re-exported.
- **Did not republish 152 or 163.** Re-deriving gives identical numbers without
  writing, and their stored rows are still stale.

**Not done / watch out:**

- **Topic 163 still needs splitting into three topics** — 20 of its 21 wrong
  edges go away with no code. Nothing in this session fixed 163.
- **`gold_topic_152.json` is a snapshot of the 2026-09-21 upload.** Re-uploading
  that lesson changes the live concepts but not the fixture. Re-export with
  `export_live_concepts` and say in the log that the numbers moved because the
  fixture moved.
- **Head words can be ordinary adjectives.** `Small intestine → Spine` is
  accepted at 0.5 because "small intestine" lends "small" a full name-weight
  reference. `head_words` checks only that a head word is unambiguous among the
  concept *names*, not that it is a term. Untouched, now recorded in §7.
- **"Comparing the Three States" is also split on topic 152** (331 and 403),
  the same defect as 318/319, and not in the earlier notes.
- The old `gold_map_*.json` / `gold_topic_*.json` pair for 62 and 79 still comes
  from `export_gold_concepts`. Both commands are now live and they write
  **different shapes**; the fixture's `concept_keys` key tells them apart.
- I did not touch `backend/course/tests.py` (the user's uncommitted work) or
  `frontend/`.

### 2026-09-22 — Claude Code — merge `mavia-latest`: the adaptive engine is real now, and three docs said otherwise

**Branch / commits:** `jean-jure-latest`, `3963207` (criteria work) then
`23eb3df` (merge of `origin/mavia-latest` @ `6eea641`). Clean merge, **no
conflicts** — `mavia-latest` had already merged this branch's base (`3820565`)
at `e4e74f1`, so only three of their commits were new.

**Tests:** `python manage.py test -v 1` on the merged tree → **889 passing** (845 ours before the merge; their adaptive suites added, `adaptive_portal`'s 260 lines and `user/test_email_verification.py` removed with them). All three `test_gold_paths` lessons still `ok`, so the criteria work survived the merge intact.

**Live database: untouched by me.** But note that the merge brings **six new
`adaptive` migrations** (through `0006_decision_log_and_concept_mastery`), so
`backend/db.sqlite3` is behind the models until someone runs `migrate`. I did
not run it. Backups are beside it.

**What the merge actually brings**

The user asked whether our dead adaptive code could now be deleted. **It
cannot, because it is no longer dead — and the part that genuinely was dead has
already been deleted by the groupmate.** Specifically:

- **`adaptive/` is now the implementation, not a stub.** `services.py`
  271 → 882 lines, `models.py` 73 → 249, `views.py` → 403, plus
  `PATH_MODE.md` and three new test modules (`test_path_mode.py` 738,
  `test_mobile_traversal.py` 376, `test_decision_log.py` 174).
- **BKT is real.** `adaptive/services.py::_bkt_update`, reading
  `adaptive_config.AdaptiveConfig` for `p_guess` / `p_slip` / `starting_mastery`.
  `adaptive_config` had a docstring saying "when that engine is ported into
  mavia, its scorer should read `AdaptiveConfig.load()`" — it now does, so that
  app stopped being speculative too.
- **`adaptive_portal/` is gone**, all 13 files. That was the old flat
  PDF-order walker and it was the genuinely dead one.
- **DQN is still not wired into serving.** The RL work is in `notebook/mavia_rl/`
  (env, agent, train, evaluate, validate); nothing under `backend/` imports it.
  Checked, not assumed.
- `frontend/` is renamed to **`web-app/`**. A `frontend/` directory survives on
  disk holding only `node_modules/` and is no longer tracked.

**Changed (docs only — I wrote no code this half of the session):**

Four stale statements, each of which the merge turned from true into
false, and each of which would have misled the next agent:

1. `learning_path/CRITERIA.md` §"Not done yet" — said the student apps
   "still walk learning objects in PDF order and have not been switched to it",
   naming `adaptive_portal/services.py`, which no longer exists.
2. `learning_path/HANDOFF.md` §6 — same claim, same dead module. Both now say
   the path contract *is* read, and point at `adaptive/PATH_MODE.md`. This one
   matters beyond tidiness: `HANDOFF.md` documents the published payload, and
   the adaptive engine now depends on it, so changes to it are no longer free.
3. `docs/PROJECT_CONTEXT.md` §2 item 7 — said "BKT + DQN ... **do not exist in
   the codebase yet**. Do not assume they are there." Half of that is now
   backwards. Rewritten to say what path mode does, that BKT is implemented and
   tunable, and that DQN specifically is still notebook-only.
4. `docs/PROJECT_CONTEXT.md` §"Tech stack" and §"Running things" — **found while
   checking the other three, not in the original list.** Every frontend path was
   `frontend/`, and `npm run build` in a directory that now has no
   `package.json`. Also described an `index.css` mirror that no longer exists;
   `web-app/src/main.jsx` imports `styles/mavia.css` and `styles/pipeline.css`.

**Decisions I made:**

- **Merged rather than rebased**, keeping the criteria commit separate from the
  merge. Cost if wrong: an extra merge commit in the history.
- **Did not run `migrate`.** The live database is the teacher's working copy and
  the six new adaptive migrations are the groupmate's; running them is a state
  change nobody asked for. Cost if wrong: anyone starting the server hits
  "Your models have changes that are not yet reflected in a migration" or a
  missing-table error until they migrate.
- **Fixed a fourth stale doc** beyond the three asked for, because it was the
  same defect from the same merge and would have sent the next agent to a
  directory with no `package.json`.
- **Did not delete the leftover `frontend/` directory.** It is untracked and
  holds only `node_modules/`; deleting several hundred MB the user did not ask
  about is their call, and it is recorded in §"Tech stack".

**Not done / watch out:**

- **`backend/db.sqlite3` needs `python manage.py migrate`** before the server
  runs against the merged models. Not done deliberately (above).
- **`backend/course/tests.py` still holds the user's uncommitted work** and was
  never staged, before or after the merge.
- The merged tree has a `web-app/` and a stale `frontend/node_modules/`; the
  latter can be deleted whenever convenient.
- I reviewed the adaptive code only far enough to check the four doc claims
  (BKT present, `AdaptiveConfig` read, `adaptive_portal` gone, DQN not wired).
  **I did not review the groupmate's adaptive work for correctness**, and this
  entry should not be read as saying it is sound.

---

## Open threads

- **Regenerate figure descriptions** with the new RTCF prompt (needs Ollama's
  vision model). Clears the model's chatter from lesson text and from one
  concept's name. A before/after measurement (length, preamble, overlap with
  the lesson) would turn the prompt rewrite into a measured claim for the
  manuscript; not yet run. — raised by Claude Code, 2026-09-21
- **Split topic 163 into three topics**, one per organ system, in the UI. It is
  three lessons in one topic, and every criterion assumes a topic *is* a lesson.
  Measured 2026-09-22: 41 accepted edges → 22, and all 20 cross-lesson edges
  disappear, with no code. This is the single largest remaining wrong-edge
  source and nothing in the criteria can reach it. — raised by Claude Code,
  2026-09-22
- **Have the teacher check `gold_map_152.json`.** Its concept map was
  reconstructed from `gold_map_62` (same lesson) plus the merges the revision
  doc records, not stated by the teacher for today's objects. Five of seven
  member counts match 62 exactly; `changing` and `examples` differ because
  extraction chunked differently. It is now an acceptance test, so it should be
  confirmed before the manuscript quotes it. — raised by Claude Code, 2026-09-22
- **Join topic 152's split concepts** (318/319 "Changing From One State to
  Another", and 331/403 "Comparing the Three States") with **Move into another
  concept…**. Joining 318/319 closes `comparing → changing`, topic 152's last
  missing required edge. When it closes, empty `known_missing` in
  `gold_map_152.json` and re-export the fixture, or `test_gold_paths` fails on
  the gap closing — which is what that assertion is for. — raised by Claude
  Code, 2026-09-22
- **Empty-group cleanup on material delete** — proposed, awaiting the user's
  go-ahead. Less urgent than it looked: the re-upload refilled all 31, so none
  are empty now. The underlying bug stands, and group 326 did inherit a stale
  `auto_label`, which is exactly the risk described. — raised by Claude Code,
  2026-09-21
- **A third lesson** is what the learning-path criteria actually need; they are
  currently fitted to the same two lessons the gold standard came from. Re-run
  `python manage.py evaluate_gold_paths` when one exists. — raised by Claude
  Code, 2026-09-21. *Partly addressed 2026-09-22*: `test_gold_paths` now runs a
  third fixture, but it is the **same lesson** as topic 62 in the shape the
  pipeline derives today, so it tests a different failure mode, not a different
  lesson. A genuinely unseen lesson is still wanted. Note also that
  `evaluate_gold_paths` still sweeps only topics 62 and 79.
- **Head words can be ordinary adjectives.** `Small intestine → Spine` is
  accepted at `ref_forward` 0.5 because "small intestine" lends "small" a full
  name-weight reference to anything saying "small". `head_words` only checks
  that a head word is unambiguous among the concept *names*, not that it is a
  term at all. Cheap to fix, not measured, not attempted. — raised by Claude
  Code, 2026-09-22
