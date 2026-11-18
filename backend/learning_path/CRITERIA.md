# Prerequisite link criteria (v7.0, as frozen on 2026-11-19)

**Scope:** one path per **topic**, whose steps are **concepts**. A concept is one group of
learning objects exactly as the lesson pipeline stored it, named by the group's label
(`services/concept_units.py`). The learning path reads the groups and their learning objects and
nothing else: it does not join groups, rename them, or read the PDF or its extracted blocks.

**The idea:** two questions, two kinds of evidence. **Whether** a link exists comes from the text
(a name, words one concept owns, a heading). **Which way** it points comes from the lesson's
order. A text link nothing contradicts is accepted; a contradicted one goes to the teacher as a
suggestion. The clues follow RefD (Liang et al., EMNLP 2015): A is a prerequisite of B when B refers
to A more than A refers to B.

## The steps

| Step | Code | What it does |
|---|---|---|
| 1 Document order | `concept_units._order_records` | The PDFs are read in reference order (most concepts, then earliest upload). Each PDF's concepts are placed in its own order; a concept the reference PDF lacks goes right after the concept its own PDF puts before it, else before the next placed one, else at the end. |
| 2a Words | `concept_text.prepare` | Sentences (≥ `LEARNING_PATH_MIN_SENTENCE_WORDS` = 4 words), Porter-stemmed content words (English stop words and words under `MIN_TERM_LETTERS` = 3 letters dropped), the concept's name (≤ `MAX_NAME_TERMS` = 6 stems), its members' titles (part names), and the headings its members sit under (`section_title`). |
| 2b Owned words | `clues.find_term_owners`, `clues.log_likelihood` | For each word two or more concepts use: each concept using it at a higher rate than the rest of the topic is tested with SciPy's G-test (log-likelihood ratio, Dunning 1993); the owner is the concept with the highest G² among those significant at p < .05 (`SIGNIFICANCE_LEVEL`). |
| 2c Facts | `criteria._facts` | Per pair, read as earlier/later by the PDFs that teach both (document order when none does): name use and owned-word use each way (shares of sentences/passages), single-word use, heading containment, 2+ PDF agreement, `parallel`, figure or not. |
| 2d Verdict | `fusion.reference_verdict` | The table below. |
| 3 Save | `publishing.refresh_prerequisites`, `publishing.hold_loop_links` | Teacher decisions are kept. Accepted links are added to a graph that starts with the approved ones, forward links first; one that would close a loop is saved as pending with the contradiction `loop`. |
| 4 Order | `publishing.order_with_links` | Kahn's topological sort over accepted and approved links; the ready concept with the smallest document position goes next (min-heap). Fewer placed than exist raises `LoopError`. Level = 1 + the highest level among a concept's prerequisites. |

The adaptive engine then walks the saved path (`adaptive/services.py`).

## The evidence

| Evidence | Reads | Known failure |
|---|---|---|
| name | a concept's sentences containing all of the other's name stems, or all stems of one of its parts' titles when both share a PDF | lessons refer to a concept by its parts' contents ("anther", not "stamen"); sentence-like titles have no name; Porter stems "gas" to `ga` and "gases" to `gase`, so they do not match |
| owned words | a concept's passages using **at least two** words the other owns (`MIN_SHARED_TERMS` = 2) | an overview uses its children's words; AI-written figure descriptions add stock words ("columns, rows"); a link on single shared words is only a suggestion (`weak_terms`) |
| heading | a concept whose members sit under a heading whose stems contain the other's name | depends on the stored `section_title`; a concept's own title can name another concept (the figure "… States of Matter") |
| PDF order | positions in every PDF teaching both | a teacher-added object sits at the end of its PDF |
| `parallel` | both under one heading naming neither | depends on section headings |

The four settings are defaults, not values from a study. A one-step sensitivity check (each value
±1 on topics 2, 19 and 10, 17 Nov) changed nothing for sentence words and name stems, 8 decisions for
term letters 4 ("Gas" loses its name) and up to 13 for shared words 1. Word normalisation was tested
on topic 2 (19 Nov): no stemming 22 accepted / 24 pending, Porter 25 / 15, WordNet lemmas with
part-of-speech tags 27 / 17 with one correct link lost and one wrong sibling link added; Porter was kept.

## The verdict

| # | Situation | Verdict | Direction |
|---|---|---|---|
| 1 | `parallel` | no link | — |
| 2 | the only text link is single shared words | pending (`weak_terms`) | the order |
| 3 | text silent, 2+ PDFs agree on order | pending | the PDFs' order |
| 4 | text silent, otherwise | no link | — |
| 5 | heading containment | **accepted** | the heading's |
| 6 | the later concept refers to the earlier (name or owned words), no contradiction | **accepted** | earlier → later |
| 7 | any other text reference | pending | suggested, see below |

Contradictions: `reverse_name` (the earlier names the later more than the reverse), `pdfs_disagree`,
`backward_only` (only the earlier refers), and `loop` (added at save time). A suggestion's direction:
a figure goes after the text its description refers to; otherwise the document order (`pdf_order`
when a PDF teaches both, else `merged_order`).

Each stored link's `evidence` holds `rule: "reference-order"`, `version: "7.0"`, `direction_from`
(`heading`, `pdf_order`, `figure`, `merged_order`, `pdf_agreement`), `contradictions`, the clue votes
(prerequisite first) and each clue's numbers. `reasons.link_reason` turns that into one sentence for
the review screen.

Consequence: accepted links point forward in the lesson, except heading links, so the automatic
order is the document order except where a heading moves a concept. What the criteria add is the
prerequisite graph and the levels.

## Edge status and teacher control

`ConceptPrerequisite` rows: `accepted` and `pending` come from the criteria and are replaced on every
derivation; `approved` and `rejected` come from a teacher and are never overwritten. Only `accepted`
and `approved` shape the order. Every teacher action that adds a link refuses one that closes a loop
(`services/teacher_links.py`).

## Results on the final data (19 Nov 2026, no teacher decisions)

| Topic | Concepts | Accepted | Pending | Held for a loop |
|---|---|---|---|---|
| 2 Solid, Liquid and Gas | 11 | 25 | 15 | 0 |
| 19 Reproduction Among Flowering Plants | 7 | 4 | 17 | 0 |
| 10 Separating Mixtures | 12 | 19 | 7 | 0 |

## Course level (across topics): deferred

Links between topics of one course are deferred to the 100% version. The earlier design (removed
2026-10-20, restorable from commit `5e45893`) stored every derived course link as a suggestion. The
`CourseConceptLink` table is kept.

## Version history

- **v6.2** (2026-10-06): the text decides whether, the order which way; two owned words needed; part names within a shared PDF.
- **v6.3** (2026-10-21): printed-in headings read from the stored PDF blocks.
- **v7.0** (2026-11-12): reads only the lesson pipeline's groups and learning objects; printed-in headings and group joins removed.
- **17–19 Nov 2026, same rule version:** loops held as pending at save time instead of breaking the least confident link at sort time; textbook Kahn's with a min-heap; document order follows the reference PDF; the unused meaning, relatedness and confidence numbers, the cross-section flag and the name-based direction rule removed; owned words decided by SciPy's G-test at p < .05 instead of G² ≥ 3.84 (identical results on topic 2).
