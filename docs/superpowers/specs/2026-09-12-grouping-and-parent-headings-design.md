# Parent headings, plural labels, and margin-gated suggestions

**Date:** 2026-09-12
**Status:** Approved, pending implementation
**Files touched:** `lessons/services/content_generator.py`, `lessons/services/semantic_grouping.py`

## Problem

Three PDFs on the same topic produced five cross-material groups, all between PDF 1
and PDF 2. PDF 3 (`states-of-matter-accessible`) joined nothing, and the four
suggestions it did generate for teacher review were all wrong.

Three independent defects cause this.

### 1. Extraction discards parent headings

`build_learning_objects_from_pdf_blocks` clears `active_section_title` at every
heading, including sub-headings that should inherit it. The title is only ever
*set* on the inline-definition path (`Shape: Fixed`), so a numbered section
followed by prose sub-headings loses its parent entirely.

Observed in PDF 3: sections 5-7 keep their parent, sections 1-4 do not, leaving
six `Diagram description` and five `Everyday examples` objects with
`section_title=''` and nothing recording which state each belongs to.

The same mechanism also *over-fires*: because the pending title is never cleared
between siblings in a flat list, PDF 2's glossary made `physical change` the
section of the eight terms following it, and of itself.

### 2. Label corroboration cannot see plurals

`_label_corroborated_decision` is what produced every correct group currently in
the database (all five PDF 1 <-> PDF 2 pairs). It compares
`normalize_learning_object_title` output with exact equality, and that helper does
no plural folding:

    'Solids' -> 'solids'   !=   'Solid' -> 'solid'

PDF 3 titles its sections in the plural, so corroboration is structurally
incapable of firing for it. This is the direct cause of the missing correct pairs.

### 3. Wrong pairs are certified instead

With titles excluded from scoring (`title_used: false`), content-only similarity
matches PDF 3's particle-focused prose to PDF 1's *comparison table rows* rather
than its definitions. The mutual-best-match gate then certifies them, because the
nomination genuinely is reciprocal.

| source | candidate | score | margin |
|---|---|---|---|
| `Particle arrangement` | `Solids` | 0.565 | 0.259 |
| `Everyday Examples` | `Example` | 0.547 | 0.178 |
| `Flow` | `Liquids` | 0.489 | **0.0015** |
| `Volume` | `Gases` | 0.448 | 0.092 |

`Flow <-> Liquids` won by 0.0015 despite a configured `minimum_margin` of 0.05,
so that floor is not enforced on review-tier suggestions.

## Design

### Change A: inherit parent headings

Track a pending parent across the block loop. A heading becomes the parent when it
is a numbered section heading; a non-numbered heading that creates an object
inherits the pending parent instead of clearing it. The parent carries its own
title, matching the convention already pinned by the `Sense Organs` test.

Cleared by: the next qualifying numbered heading, a structural metadata label, or
the start of an excluded section.

**Required guard.** `_learning_object_heading_title` returns a numbered title
*before* it checks category, so an unguarded rule would promote assessment items
(`1. ______ condense`, `8. Quick Check Questions`, `9. Answer Key`) into section
parents. A heading may become a parent only when it is `category=lesson_content`
**and** `include_in_narration=True`.

Accepted cost: `1. What is Matter?` is misclassified as `assessment` by the block
classifier, so it will not become a parent. Correcting the classifier is a
separate and deeper problem, deliberately out of scope.

**Both creation paths must carry the parent.** Fixing the heading path alone was
not enough: `Diagram description` and `Everyday examples` are not heading-derived
at all. They are `Label: value` inline definitions, built on a separate path that
reads `active_section_title`, which is reset at every heading. That path falls
back to the section parent when it has no sibling-run title of its own.

A numbered heading that does *not* qualify still closes the open section rather
than inheriting it, so a misclassified `6. Changing From One State to Another`
does not become a child of section 5.

### Change A2: stop the over-firing

The pending parent must not persist across siblings in a flat list. A list of
peer definitions (a glossary) must not adopt its first entry as the parent of the
rest.

### Change B: plural-tolerant corroboration

Add a helper, used *only* by label corroboration, that singularizes the final word:

- `-ies` -> `y`
- `-(s|ss|sh|ch|x|z)es` -> strip `es`
- otherwise trailing `-s` (never `-ss`)

Verified against all 60 real object titles: `Solids->solid`, `Liquids->liquid`,
`Gases->gas`, with no collisions between genuinely different concepts.

Two comparison sites must change together: the label derived in
`_specific_normalized_label`, and the raw comparison inside
`_label_corroborated_decision`. `_GENERIC_INSTRUCTIONAL_LABELS` must be folded
through the same helper, otherwise `'everyday examples'` (currently excluded as
generic) singularizes to `'everyday example'`, which is absent from the set, and
becomes eligible.

**`normalize_learning_object_title` itself is not modified.** It has 14+ callers
including question-to-object matching, prior-group restoration on regeneration,
and answer-line detection. Global plural folding there is a far wider blast radius
than this problem justifies.

### Change C: enforce the margin floor

Apply the configured `minimum_margin` to review-tier (MEDIUM) suggestions, so a
near-tie such as `Flow <-> Liquids` (0.0015) is not put in front of a teacher.

## Blast radius

`section_title` feeds narration wording (`"In X. Title: ..."`), M4
section-progression MEDIUM edges in `learning_path/services/evidence.py`, the
path-builder tie-break, linker structure similarity, and grouping. Prerequisite
edges will change.

Change A requires re-extracting all three PDFs, which rebuilds objects, groups and
suggestions; teacher review state on the current ones is lost. Change B and C do
not require re-extraction.

## Verification

Baseline before any edit: **143 tests passing**, including three that pin
`section_title` behaviour on non-numbered headings. A rule keyed to *numbered*
headings should leave them untouched; this is to be verified, not assumed.

New tests cover: numbered-parent inheritance, the assessment guard, the flat-list
over-fire, plural corroboration, generic-label folding, and the margin floor.

After implementation, re-extract all three PDFs and diff grouping before/after.
The success criterion is that `Solids`/`Liquids`/`Gases` group with
`Solid`/`Liquid`/`Gas`, and that the four wrong suggestions disappear.
