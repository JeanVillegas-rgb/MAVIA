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

---

## Open threads

- **Regenerate figure descriptions** with the new RTCF prompt (needs Ollama's
  vision model). Clears the model's chatter from lesson text and from one
  concept's name. A before/after measurement (length, preamble, overlap with
  the lesson) would turn the prompt rewrite into a measured claim for the
  manuscript; not yet run. — raised by Claude Code, 2026-09-21
- **Publish both topics** once TTS is reachable, then compare the derived paths
  with the gold standard and append the result to
  `docs/learning_path_revision_2026-09-17.md`. As of 2026-09-21 both topics are
  content-complete and the TTS retry is in, so nothing known is blocking a
  publish — but no publish has actually succeeded yet, and `LearningPathStep`
  is still empty, so the derived path has never once been compared with the
  gold standard. That comparison is the real open item. — raised by Claude
  Code, 2026-09-21
- **Empty-group cleanup on material delete** — proposed, awaiting the user's
  go-ahead. Less urgent than it looked: the re-upload refilled all 31, so none
  are empty now. The underlying bug stands, and group 326 did inherit a stale
  `auto_label`, which is exactly the risk described. — raised by Claude Code,
  2026-09-21
- **A third lesson** is what the learning-path criteria actually need; they are
  currently fitted to the same two lessons the gold standard came from. Re-run
  `python manage.py evaluate_gold_paths` when one exists. — raised by Claude
  Code, 2026-09-21
