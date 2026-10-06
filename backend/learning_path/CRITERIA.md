# Prerequisite link criteria (v6.2)

**Status (2026-10-06):** v6.2 is the default (`rule="cleaner-edges"`): v6.1 plus part names
(below). v6.1 was the default from 2026-10-02; v6 stays selectable as
`rule="reference-order"`. Branch `learning-path-graph-screen` (not merged).
v6.1 design: `docs/superpowers/specs/2026-10-02-learning-path-v6-1-edge-quality-design.md`;
measurements: `docs/learning-path-v6-1-evaluation-2026-10-02.md` (final check on new topics 351/353/365).
v6:
Design: `docs/superpowers/specs/2026-09-30-learning-path-v6-reference-order-design.md`.
Plan: `docs/superpowers/plans/2026-09-30-learning-path-v6-reference-order.md`.
Measurements: `docs/learning-path-v6-evaluation-2026-09-30.md` and
`docs/learning-path-v6-evaluation/`. Numbers live there, not here.

**Scope:** one path per **topic**, whose steps are **concepts** (grouping's concept bundles
across every PDF of the topic, `services/concept_units.py`). Grouping and extraction are read,
never changed.

**The idea:** two questions, two kinds of evidence. **Whether** a link exists comes from the text
(a name, terms one concept explains, a heading). **Which way** it points comes from the lesson's
order. A text link nothing contradicts is accepted; a contradicted one goes to the teacher.

## The steps

| Step | Code | What it does |
|---|---|---|
| 0 Prepare | `concept_text.py`, `embeddings.py` | Sentences (≥ 4 words) with their PDF, Porter-stemmed terms (general English stopwords only), sentence vectors from our own loader of the pinned `all-MiniLM-L6-v2`, each PDF's order of concepts. |
| 1 Facts | `clues.py` | Per pair, read as earlier/later in the PDF(s) both appear in (the topic's merged order when they share none): name use and owned-term use each way, heading containment, 2+ PDF agreement, `parallel` flag, figure or not. |
| 2 Verdict | `fusion.reference_verdict`, `criteria.py` | The table below. |
| 3 Clean-up | `publishing.py` | Loops broken at the least confident derived link; links a longer chain implies are flagged `redundant` (hidden on the graph). |
| 4 Order | `publishing.order_with_links` | Kahn's topological sort; ties go to the topic's merged PDF order. |

Relatedness (`relatedness.py`) and the meaning clue are **recorded, not counted**. Without the
encoder the verdicts are the same; only those two numbers are missing.

The adaptive engine then walks the saved path and detours through the nearest prerequisite
(`adaptive/services.py`), unchanged.

## The evidence

| Evidence | Reads | Known failure |
|---|---|---|
| name | a concept's sentences containing all of the other's name stems, or (v6.2) all stems of one of its parts' titles when both concepts share a PDF | lessons refer to a concept by its parts' contents ("anther", not "stamen"); sentence titles have no name; a one-word part title ("Example") matches ordinary words |
| terms | a concept's passages using **at least two** terms the other owns (Dunning G² ≥ 3.84; v6.1, `MIN_SHARED_TERMS`) | an overview uses its children's terms; a link on one shared word is only a suggestion (`weak_terms`) |
| heading | a concept under a heading whose stems contain the other's name | word matching; silent when headings are missing |
| PDF order | positions in every PDF teaching both | a figure's position (extraction puts it first); a merged order across PDFs is a guess |
| `parallel` | both under one heading naming neither | depends on section headings |

**Part names (v6.2, 2026-10-06).** The extractor stores a term in its learning object's title and the
explanation in its content, so "Pollination" is never written in Pollination's own text and a concept
bundling Pollination, Fertilization, Seed formation and Fruit formation could not be named by them.
Each member's title (numbering and "(Part n of m)" removed, at most 6 stems, not the concept's own
name) is now also a name of the concept, but only towards concepts sharing a PDF with it: across
PDFs the direction is the merged order's guess, and there "The Digestive System" (members Mouth,
Stomach, ...) became the accepted prerequisite of every concept mentioning the stomach, the module's
introduction among them. `records.name.parts` lists the part titles named; the reason reads
"Everyday Examples names Pollination and Fertilization, parts of How Flowering Plants Reproduce."
v6 and v7 do not read part names. Checked on 11 topics (live 98/115, backup 2026-10-04 2-27):
3 links gained acceptance (How Flowering Plants Reproduce -> Everyday Examples, Comparing the Three
States -> Summary, The Digestive System -> Helper organs of digestion), 1 accepted link resting on
table-layout words ("columns", "labelled") became a suggestion, 7 topics unchanged. The rule was
shaped after seeing those topics, so they are not a fair test of it.

## The verdict

| # | Situation | Verdict | Direction |
|---|---|---|---|
| 1 | `parallel` flag | no link | — |
| 2 | text silent, 2+ PDFs agree on order | pending | the PDFs' order |
| 2b | (v6.1) the only text link is single shared words | pending (`weak_terms`) | the order |
| 3 | text silent, otherwise | no link | — |
| 4 | heading containment | **accepted** | the heading's |
| 5 | the later concept refers to the earlier (name or owned terms), no contradiction | **accepted** | earlier → later |
| 6 | any other text reference | pending | see below |

Contradictions: `reverse_name` (the earlier names the later more than the reverse), `figure`
(either is a figure), `no_shared_pdf`, `pdfs_disagree`, `backward_only` (only the earlier refers).
v6.1 drops `figure` and `no_shared_pdf` (a figure pair is accepted in PDF order, a cross-PDF pair
in the merged order) and adds `weak_terms` (row 2b). Its rows carry `evidence.version = "6.1"`
and `records.terms` gains `owned_back`, `single_word_use`, `single_word_use_back`.
A suggestion's direction: a figure after the text its description refers to, else the order;
across PDFs the name clue's direction if it has one, else the merged order; otherwise the order.

Each stored link's `evidence` holds `rule: "reference-order"`, `direction_from` (`heading`,
`pdf_order`, `figure`, `name`, `merged_order`, `pdf_agreement`), `contradictions`, the clue votes
(oriented prerequisite-first), each clue's numbers, `relatedness`, `confidence` (share of the
deciding clues that agree; used to break teacher-made loops) and `semantic`.
`reasons.link_reason` turns that into one sentence for the review screen.

Consequence: derived links follow the PDF order, so the automatic order is the PDF order except
where a heading moves a concept. What v6 adds is the prerequisite graph the adaptive engine detours
through.

## Edge status and teacher control

Unchanged since v4. `ConceptPrerequisite` rows: `accepted` and `pending` come from the criteria and
are replaced on every derivation; `approved` and `rejected` come from a teacher and are never
overwritten. Only `accepted` and `approved` shape the order. Loops made of teacher links are
refused (`services/teacher_links.py`). Screen: `docs/superpowers/specs/2026-09-29-learning-path-graph-screen-design.md`.

## Course level (across topics)

Spec: `docs/superpowers/specs/2026-09-30-course-learning-path-design.md`.
`services/course_criteria.py` pairs concepts of **different** topics of one course, with the same
relatedness gate and content clues; the structure is the teacher's **outline order** (headings and
PDF order cannot compare topics). Only name and terms vote, and they must agree (spec amendment 1):
with the outline → accepted; against it → pending, `contradicts_outline`; anything else → no link.
The meaning clue is recorded, not counted. Stored as `CourseConceptLink` (`services/course_links.py`), shown on the Course path page,
refreshed when a topic is published. `published.course_prerequisites` gives the adaptive engine
accepted/approved earlier-topic prerequisites (hand-off: `docs/handoff-course-prerequisites.md`).

A second rule, `rule="shortlist"` (`services/course_shortlist.py`, spec
`docs/superpowers/specs/2026-10-02-course-path-top-down-design.md`), compares two topics only when
their outline titles are close and offers each later concept its 3 closest earlier concepts. It
failed its final check on 2026-10-03 (hit 4/18 against a bar of 0.6; the title gate closed two
related pairs and meaningless titles filled the slots), so `"strict"` stays the default. Report:
`docs/course-path-v2-evaluation-2026-10-03.md`. Final pairs 351-353, 341-343, 341-347, 347-348,
351-365, 353-365 are spent.

A third rule, `rule="closest"` (`services/course_closest.py`, spec
`docs/superpowers/specs/2026-10-03-course-path-closest-match-design.md`), links each later concept to
the earlier concept whose lesson text is closest: accepted when it is also named and shares two
distinctive words, suggested when it is 0.10 closer than the later concept's own topic-mates. On the
design pairs it accepted 3 links, all right, and none between unrelated topics. It failed its final
check on 2026-10-03: the six new pairs needed 5 links, all implicit (an idea reused without its
words), and it offered none of them (strict: none either, and 5 wrong accepted links). `"strict"`
stays the default. Report: `docs/course-path-closest-evaluation-2026-10-03.md`. Final pairs (live ids)
2-10, 3-10, 5-7, 13-19, 15-19, 19-27 are spent.

**Since 2026-10-03 every derived course link is stored as a suggestion** (`course_links.DERIVED_STATUS`,
the user's decision after the two failed final checks, not a pre-registered outcome). A rule's
"accepted" verdict still shows in its evidence and its reason text, but only a teacher's approval
lets a course link reach learners (`published.course_prerequisites` reads accepted and approved;
nothing is stored as accepted any more).

## v7 (tried 2026-10-01, not adopted)

Three equal direction votes (hierarchy, order, references) on a block x concept matrix
(`services/direction_votes.py`, `rule="three-votes"`, spreadsheet export
`export_direction_sheet`). On the design set it repaired fewer moved links than v6 (3 vs 7):
the reference and subsumption votes both read overviews backwards and outvoted correct orders
and headings. Spec `docs/superpowers/specs/2026-10-01-learning-path-v7-direction-votes-design.md`;
outputs `docs/learning-path-v7-evaluation/`. Its final check (moves 5-9 on 341-348) is unspent.

## History

- v3 (three equal votes: temporal order, RefD key terms, foundationality):
  `docs/superpowers/specs/2026-09-13-prerequisite-criteria-v3-design.md`,
  revisions in `docs/learning_path_revision_2026-09-17.md`. Retired because all three votes
  collapsed into document order.
- v4 (R1 definition, R2 heading containment, R3 reference; accepted by R1/R2):
  `docs/superpowers/specs/2026-09-29-learning-path-criteria-v4-design.md`. Retired because only
  heading containment ever accepted a link and every rule was string matching.
- v5 (relatedness gate + two evidence families):
  `docs/superpowers/specs/2026-09-30-learning-path-evidence-fusion-design.md`. Retired because
  text-only links stayed pending, suggestions piled up (62 on topic 340) and the meaning clue was
  at chance on direction.
- v6 (this document), 2026-09-30.
