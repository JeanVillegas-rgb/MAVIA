# Prerequisite link criteria (v6.2)

**Scope:** one path per **topic**, whose steps are **concepts** (grouping's concept bundles
across every PDF of the topic, `services/concept_units.py`). Grouping and extraction are read,
never changed.

**The idea:** two questions, two kinds of evidence. **Whether** a link exists comes from the text
(a name, terms one concept explains, a heading). **Which way** it points comes from the lesson's
order. A text link nothing contradicts is accepted; a contradicted one goes to the teacher.

## The steps

| Step | Code | What it does |
|---|---|---|
| 0 Prepare | `concept_text.py`, `embeddings.py` | Sentences (≥ 4 words) with their PDF, Porter-stemmed terms (general English stopwords only), the names of the concept and of its parts, sentence vectors from our own loader of the pinned `all-MiniLM-L6-v2`, each PDF's order of concepts. |
| 1 Facts | `clues.py` | Per pair, read as earlier/later in the PDF(s) both appear in (the topic's merged order when they share none): name use and owned-term use each way, heading containment, 2+ PDF agreement, `parallel` flag, figure or not. |
| 2 Verdict | `fusion.reference_verdict`, `criteria.py` | The table below. |
| 3 Clean-up | `publishing.py` | Loops broken at the least confident derived link; links a longer chain implies are flagged `redundant` (hidden on the graph). |
| 4 Order | `publishing.order_with_links` | Kahn's topological sort; ties go to the topic's merged PDF order. |

Relatedness (`relatedness.py`) and the meaning clue are **recorded, not counted**. Without the
encoder the verdicts are the same; only those two numbers are missing. Their cutoffs are stored in
`calibration/weights.json` (measured 2026-09-30).

The adaptive engine then walks the saved path and detours through the nearest prerequisite
(`adaptive/services.py`).

## The evidence

| Evidence | Reads | Known failure |
|---|---|---|
| name | a concept's sentences containing all of the other's name stems, or all stems of one of its parts' titles when both concepts share a PDF | lessons refer to a concept by its parts' contents ("anther", not "stamen"); sentence titles have no name; a one-word part title ("Example") matches ordinary words |
| terms | a concept's passages using **at least two** terms the other owns (Dunning G² ≥ 3.84, `MIN_SHARED_TERMS`) | an overview uses its children's terms; short passages rarely hold two, so a link on single shared words is only a suggestion (`weak_terms`) |
| heading | a concept under a heading whose stems contain the other's name | word matching; silent when headings are missing |
| PDF order | positions in every PDF teaching both | a figure's position (extraction puts it first); a merged order across PDFs is a guess |
| `parallel` | both under one heading naming neither | depends on section headings |

**Part names.** The extractor stores a term in its learning object's title and the explanation in
its content, so "Pollination" is never written in Pollination's own text. Each member's title
(numbering and "(Part n of m)" removed, at most 6 stems, not the concept's own name) is also a name
of the concept, but only towards concepts sharing a PDF with it: across PDFs the direction is the
merged order's guess, and there "The Digestive System" (members Mouth, Stomach, ...) became the
accepted prerequisite of every concept mentioning the stomach. `records.name.parts` lists the part
titles named; the reason reads "Everyday Examples names Pollination and Fertilization, parts of How
Flowering Plants Reproduce."

## The verdict

| # | Situation | Verdict | Direction |
|---|---|---|---|
| 1 | `parallel` flag | no link | — |
| 2 | the only text link is single shared words | pending (`weak_terms`) | the order |
| 3 | text silent, 2+ PDFs agree on order | pending | the PDFs' order |
| 4 | text silent, otherwise | no link | — |
| 5 | heading containment | **accepted** | the heading's |
| 6 | the later concept refers to the earlier (name or owned terms), no contradiction | **accepted** | earlier → later |
| 7 | any other text reference | pending | see below |

Contradictions: `reverse_name` (the earlier names the later more than the reverse),
`pdfs_disagree`, `backward_only` (only the earlier refers). Being a figure or coming from different
PDFs does not block a link: a figure pair is accepted in PDF order, a cross-PDF pair in the merged
order. A suggestion's direction: a figure after the text its description refers to, else the order;
across PDFs the name clue's direction if it has one, else the merged order; otherwise the order.

Each stored link's `evidence` holds `rule: "reference-order"`, `version: "6.2"`, `direction_from`
(`heading`, `pdf_order`, `figure`, `name`, `merged_order`, `pdf_agreement`), `contradictions`, the
clue votes (oriented prerequisite-first), each clue's numbers, `relatedness`, `confidence` (share of
the deciding clues that agree; used to break loops) and `semantic`. `reasons.link_reason` turns
that into one sentence for the review screen.

Consequence: derived links follow the PDF order, so the automatic order is the PDF order except
where a heading moves a concept. What the criteria add is the prerequisite graph the adaptive engine
detours through.

## Edge status and teacher control

`ConceptPrerequisite` rows: `accepted` and `pending` come from the criteria and are replaced on
every derivation; `approved` and `rejected` come from a teacher and are never overwritten. Only
`accepted` and `approved` shape the order. Loops made of teacher links are refused
(`services/teacher_links.py`).

## Course level (across topics)

`services/course_criteria.py` pairs concepts of **different** topics of one course. Relatedness
gates a pair (`related_cutoff`); the structure is the teacher's **outline order** (headings and PDF
order cannot compare topics). Only name and terms vote, and they must agree: with the outline →
accepted; against it → pending, `contradicts_outline`; anything else → no link. The meaning clue is
recorded, not counted. Stored as `CourseConceptLink` (`services/course_links.py`), shown on the
Course path page, refreshed when a topic is published.

**Every derived course link is stored as a suggestion** (`course_links.DERIVED_STATUS`): the rule's
verdict shows in its evidence and reason text, but only a teacher's approval lets a course link reach
learners (`published.course_prerequisites` reads accepted and approved).

## Version

v6.2 (2026-10-06): the text decides whether two concepts are linked, the lesson's order which way;
figures and concepts from different PDFs do not block a link, two owned terms are needed, and a
concept is also named by its parts' titles within a shared PDF.
