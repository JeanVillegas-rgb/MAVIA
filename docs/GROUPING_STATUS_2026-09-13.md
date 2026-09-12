# Content preparation: section headings, chunking, and grouping

**Date:** 2026-09-13
**Branch:** `jean-latest`
**Area:** PDF extraction (`content_generator.py`) and cross-PDF grouping
(`semantic_grouping.py`).

This documents seven changes so they can be traced in the code, why each was
needed, and what evidence supports it. Nothing here was changed on a hunch --
every item below came from a defect observed in live uploaded data.

---

## Files touched

| file | change |
|---|---|
| `backend/lessons/services/content_generator.py` | changes 1, 2, 3, 5 |
| `backend/lessons/services/semantic_grouping.py` | changes 4, 6, and the margin floor |
| `backend/lessons/test_section_parents.py` | **new** -- tests for 1, 2, 3, 5 |
| `backend/lessons/test_label_corroboration.py` | tests for 4, 6, and the margin floor |
| `docs/superpowers/specs/2026-09-12-grouping-and-parent-headings-design.md` | design record for changes 1-4 and the margin floor **only** -- 5 and 6 came later and are documented here, not there |

**268 tests pass.** No test was deleted. One existing assertion
(`test_unrelated_short_definitions_are_not_merged`) broke during change 5; the
code was corrected to satisfy it rather than the test being loosened.

---

## 1. Section headings survive chunking

**Trace:** `_qualifies_as_section_parent()`, the `section_parent_title` variable
inside `build_learning_objects_from_pdf_blocks()`.

**Problem.** The extractor cleared the active section at *every* heading,
including sub-headings that should inherit it. In `solliqgas`, the items under
`PARTICLE ARRANGEMENT` stored `section_title=''` -- the hierarchy existed in the
PDF and was thrown away during chunking. In `states-of-matter-accessible` it
produced six `Diagram description` and five `Everyday examples` objects with
nothing to tell them apart.

**Change.** A numbered heading opens a section; a non-numbered heading beneath it
*inherits* that section instead of clearing it.

**The guard that matters.** Being numbered is not sufficient.
`_learning_object_heading_title()` returns a numbered title *before* it checks
category, so quiz items (`1. ______ condense`), `8. Quick Check Questions` and
`9. Answer Key` also reach the heading path. A heading may open a section only
when it is `category=lesson_content` **and** `include_in_narration=True`.

A numbered heading that fails that test still *closes* the open section rather
than inheriting it, so a misclassified `6. Changing From One State to Another`
does not become a child of section 5.

**Known limitation.** `1. What is Matter?` is misclassified as `assessment` by
the block classifier, so it does not open a section. Fixing the classifier is a
separate and deeper problem, deliberately out of scope.

## 2. Both creation paths carry the section

**Trace:** `active_section_title or section_parent_title` in the
inline-definition branch.

Fixing the heading path alone was not enough. `Diagram description` and
`Everyday examples` are not heading-derived -- they are `Label: value` inline
definitions built on a separate path that read a variable reset at every
heading. That path now falls back to the section parent.

## 3. A definition never becomes its neighbours' section

**Trace:** the `from_inline_definition` flag set when an inline definition
becomes the pending object.

**Problem.** In the unit guide, `physical change` became the section of the eight
glossary terms after it **and of itself**. Cause: a page footer
(`document_metadata`) interrupted the sibling run, so that term became the
"pending" object, and the next term promoted it to section.

**Change.** A definition item is a peer of the terms around it and can never be
promoted to their section. Only an authored heading can.

This also removed false prerequisite edges, since a shared section feeds the
learning path's section-progression rule.

## 4. Plural titles match singular ones

**Trace:** `_singular_label()`, `_GENERIC_SINGULAR_LABELS`, used by
`_specific_normalized_label()` and inside `_label_corroborated_decision()`.

**Problem.** Label matching compared exact strings, and
`'solids' != 'solid'`. One PDF titles its sections in the plural
(`2. Solids`), another defines `solid`. The shortcut was structurally incapable
of firing between them.

**Change.** Fold a trailing plural on the final word only: `-ies -> y`,
`-(s|ss|sh|ch|x|z)es -> ` strip `es`, otherwise trailing `-s` (never `-ss`). A
length guard keeps short nouns intact -- without it `gas` erodes to `ga`.

Verified against all real object titles: no two different concepts collide.

**Watch out:** the generic-label set is folded through the same rule.
Otherwise `everyday examples` singularizes to `everyday example`, drops out of
the excluded set, and a generic heading becomes eligible.

**Deliberately not changed:** `normalize_learning_object_title()` itself. It has
14+ callers including question-to-object matching and prior-group restoration.
Global plural folding there is a far wider blast radius than this problem
justifies.

## 5. A heading and its short labelled items become one object

**Trace:** `_is_section_variant_item()`, `_can_merge_section_variants()`,
`_section_names_one_concept()`, wired into `_can_merge_short_learning_objects()`.
New knob: `LEARNING_OBJECT_SECTION_VARIANT_MAX_WORDS` (default 25).

**Problem.** `PARTICLE ARRANGEMENT` and its `Solid`, `Liquid`, `Gas` items were
four separate objects, each 9-13 words. Too small to teach from, too generic to
match across PDFs, and they duplicated the `Solid`/`Liquid`/`Gas` labels inside
one PDF, which made those labels ambiguous.

**Why the existing merge rule could not do it.** It requires adjacent lexical
similarity of 0.32. These items measured **0.049, 0.202, 0.049** -- they teach by
*contrast*, so each says what the others do not and they share almost no
vocabulary. Being different from one another is exactly what makes them one
teaching unit. Raising `LEARNING_OBJECT_MIN_WORDS` does not help; word count was
never the blocking gate.

**Change.** Items in one section merge when each is short, each carries a brief
label (or is the section's own heading), and they are adjacent -- regardless of
shared wording.

**The guard that matters.** The section must *name a concept*, not be a
container. `Vocabulary`, `Glossary` and `Everyday Examples` collect entries that
merely sit together and must stay separate; `PARTICLE ARRANGEMENT` names one
idea. `_section_names_one_concept()` reuses the generic-label list already in
`semantic_grouping` (lazy import, matching how that module reaches back here).

## 6. A split heading is not an ambiguous label

**Trace:** `_PART_MARKER`, `_split_series_representative()`,
`_collapsed_label_rows()`, used by `_label_corroborated_decision()`.

**Problem.** Long sections are chunked into `SOLID (Part 1 of 3)`,
`(Part 2 of 3)`, `(Part 3 of 3)`, and the part suffix is stripped before labels
are compared -- so one concept arrives looking like three objects sharing a name.
The duplicate-label guard read that as ambiguous and switched the shortcut off,
exactly where a document is most fragmented.

**Change.** Collapse such a run to its first piece. Every condition must hold:
each title carries a part marker, they agree on the total, **the number of pieces
equals the declared total**, they share one section, they number 1..N with none
missing, and they are consecutive.

Only the first piece may corroborate; later pieces are continuations, and joining
each of them would put three objects from one PDF into one concept.

**Validated on real data** (measured on an earlier four-PDF corpus that included
a glossary-style unit guide): 3 runs correctly collapsed, 5 cases correctly left
ambiguous, no false rescues. `SOLID` section vs the `Solid` particle-arrangement
item satisfies none of the conditions and stays ambiguous, which is correct --
they really are two different things.

**Follow-up fix (found by running it against live data).** The series check
needs two or more rows to recognise a run. A part whose siblings sit in groups
the decision cannot see therefore arrived *alone*, sailed through the collapse
untouched, and corroborated as though it were the whole concept --
`LIQUID (Part 3 of 3)` grouped itself with another PDF's `liquid`.
`_collapsed_label_rows()` now refuses a lone piece numbered other than 1.

The general lesson for this area: a guard that reasons over a *set* stops
guarding, silently, when the set has one element.

---

## Also changed: near-ties stay out of the review queue

**Trace:** the `review` condition in `semantic_decision()`.

A suggestion reached the teacher after winning by **0.0015** over the runner-up,
despite a configured `minimum_margin` of 0.05 -- the floor gated automatic
grouping but not review-tier suggestions. It now gates both. A coin flip is not
a finding.

---

## Status of the live data

Measured on a clean end-to-end run: all uploads deleted and re-uploaded, so
extraction, chunking and grouping all ran under the changes above with no
carried-over state.

**Corpus:** three PDFs, all confirmed. 48 objects, 34 groups.

| material | objects | objects with no section |
|---|---|---|
| m12 `Lesson-1_Solid-Liquid-and-Gas` | 7 | 7 |
| m13 `states-of-matter-accessible` | 21 | 7 |
| m14 `solliqgas (1)` | 20 | **3** |

m12 has no numbered headings, so it has no sections to inherit. m14's three are
its two images and the merged `PARTICLE ARRANGEMENT`.

### Extraction

`PARTICLE ARRANGEMENT` is now a single object reading as one teaching unit:

> **PARTICLE ARRANGEMENT** -- The particles in each state of matter behave
> differently. Solid: Particles are very close together. They mostly vibrate in
> place. Liquid: Particles are close but can move around. They can slide past
> each other. Gas: Particles are widely separated...

The standalone `Solid`, `Liquid` and `Gas` fragments no longer exist in that
PDF, which is what made those labels ambiguous inside it. m13's state-change
table rows likewise merged into one chunk.

Note: a merged object carries its section as its *title* and stores
`section_title=''`. That is pre-existing behaviour of
`_merge_learning_object_group()`, pinned by an existing test -- it is why merged
chunks count as "sectionless" above.

### Grouping

**10 cross-material groups.** The three core concepts are each one clean
three-way group:

| group | members |
|---|---|
| grp239 | m12 `Solid` · m14 `SOLID (Part 1 of 3)` · m13 `Solids` |
| grp240 | m12 `Liquid` · m14 `LIQUID (Part 1 of 3)` · m13 `Liquids` |
| grp241 | m12 `Gas` · m14 `GAS` · m13 `Gases` |
| grp238 | `Matter` · `Matter (Part 1 of 2)` |
| grp242 | `Comparing the Three States` x2 |
| grp261 | `CHANGES IN STATES OF MATTER` · `Changing From One State to Another` |

Group sizes: 24 of one, 6 of two, 4 of three. **No group holds two objects from
the same PDF** -- the cleanest structural signal available. 18 suggestions: 14
accepted, 4 pending.

For comparison, before this work the same kind of corpus produced 5
cross-material groups, the third PDF joined nothing, `Solids` never matched
`Solid`, and all 4 pending suggestions were wrong.

### The silent-drop case was caught, by two changes compounding

`Gas -> GAS` was accepted at **0.542** -- below the 0.60 automatic threshold --
through `label_corroborated`. That is exactly the match that previously vanished
without reaching either automatic grouping or the review queue.

The chain: change 5 merged away the duplicate `Gas` fragment, which re-enabled
corroboration (change 6), which carried a match content scoring alone could not.

**The automatic threshold of 0.60 was never the problem and should stay.** The
review path was.

### Confirmation state

Re-extraction clears `learning_objects_confirmed`; materials must be confirmed
again before grouping runs.

---

## Still worth being wary of

**Silent drops -- the mechanism is still there, even though the one observed
instance was caught.** A moderate-confidence match against an already-formed
group reaches neither automatic grouping nor the review queue. It is not a wrong
suggestion that can be declined; it is an invisible omission.

Cause: a medium match requires both objects to nominate each other, and an object
already in a multi-member group is skipped by the matcher, so the nomination
cannot come back.

In the latest run this did not bite -- `Gas -> GAS` at 0.542 was rescued by label
corroboration instead (see above). That rescue depended on the duplicate `Gas`
fragment having been merged away, so corroboration happened to be available.
Where corroboration is unavailable -- a genuinely ambiguous label, say -- a score
between the review threshold and 0.60 against an already-formed group still
disappears without trace.

The automatic threshold of 0.60 is correct and should stay; the review path is
what needs repair. **Not fixed.**

**The minimum-score rule.** A group is scored by its *weakest* member, so a
one-line glossary gloss can veto a strong match -- `Solids` matched its real
definition at 0.648 but was vetoed by a 7-word stub at 0.187. Deliberate, and
pinned by `test_weak_member_in_destination_group_blocks_label_path`. It bites
whenever a glossary-style PDF is loaded. **Unresolved by design, not by
oversight.**

**Granularity mismatch between PDFs -- the most consequential one.** m13's state
changes are short table rows, so change 5 merged them into a single chunk. m14
teaches the same content as full prose, so `A. Melting`, `B. Freezing`,
`C. Evaporation`, `D. Boiling` and `E. Condensation` stayed as five objects and
**cannot align with anything**. All five are stranded.

The parent headings did group (`CHANGES IN STATES OF MATTER` with
`Changing From One State to Another`), so the concept is not lost -- but its
detail is. The same material chunked at two different grain sizes cannot be
matched, and the merge rule is bounded by word count, which is what sets the
grain. **No fix attempted; it needs a decision about whether matching should
work across grain sizes.**

**Concept fragmentation across groups -- now benign, not harmful.** The pieces of
a split heading are independent objects and match independently, so one concept
still spans several groups:

| label | groups | how |
|---|---|---|
| `gas` | **1** | all three PDFs in one group |
| `matter` | 2 | Part 1 grouped, Part 2 paired with a related m13 passage |
| `solid` | 3 | Part 1 grouped with m12/m13; Parts 2 and 3 **alone** |
| `liquid` | 3 | Part 1 grouped with m12/m13; Part 2 with an m13 diagram; Part 3 **alone** |

The difference from earlier runs matters: continuation pieces now sit **alone**
rather than capturing another PDF's concept. That is the harmless failure mode --
work left undone rather than work done wrongly.

This is pre-existing: nothing has ever tied the pieces of one split heading
together for grouping. Resolving it means deciding whether the pieces should
share a group, or whether only the first piece is eligible for cross-PDF matching
with the rest represented by it. **That decision is not made yet.**

**Prerequisite edges have moved.** `section_title` feeds the learning path's
section-progression rule, and far more objects now carry one. Edges will differ
from before and have not been reviewed.

**Not verified:** content-version generation, publish, and any teacher review of
the grouping results.
