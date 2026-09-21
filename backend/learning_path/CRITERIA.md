# Prerequisite edge criteria (v3)

> **Superseded in part by the [2026-09-17 revision](#revision-2026-09-17-refd-key-term-reference):** the CSR-based semantic reference, the IOLR built on it, the cross-section cap and their thresholds describe the earlier design and are kept as history. Where they disagree with the last section, the last section is current.

**Status (2026-09-15):** criteria, vetoes and topic ordering are implemented in
`services/criteria.py` and `services/concept_units.py`. On a successful publish,
`services/publishing.py` stores the links (`ConceptPrerequisite`) and the step
order (`LearningPathStep`); only `accepted` and teacher-`approved` links shape
the order, `pending` ones are stored but hidden. The adaptive rules read the
saved path through `services/published.py` (see `HANDOFF.md`), and teachers
adjust links on the review screen (see "Teacher control" below). The earlier
per-material scheme has been removed.
Full design record: `docs/superpowers/specs/2026-09-13-prerequisite-criteria-v3-design.md`

**Scope:** one path per **topic**, whose steps are **concept groups**.

A topic holds several uploaded PDFs teaching overlapping content, and grouping
already decides which chunks across those PDFs teach the same concept. The
concept is therefore the teaching unit: a learner meets "Solid" once, with its
three versions, rather than three times from three PDFs.

Each step records which PDFs contributed to it, so a step can still be traced
back to its sources without keeping a second per-PDF view in sync.

This replaces the previous arrangement, where each PDF was ordered independently
and a topic with three files produced three disconnected paths.

### How the files' orders are merged

Each file has its own sequence, and they do not always agree -- one file opens
with a comparison figure that another shows after all three states.

1. **Two concepts that appear in the same files keep the order most of those
   files give them.**
2. **When the files split evenly, the file teaching the most concepts decides**,
   then the one uploaded first. With two files every disagreement is an even
   split, so this is the usual case.
3. **A concept only some files teach keeps its neighbours from those files** --
   it is never pushed to the end because another file lacks it.

Blending positions (averaging them, or taking the later one) was tried and
rejected: it placed "Comparing" between Liquid and Gas when one file taught it
first and the other last -- an order neither author wrote. Settling every pair
by a real file's order means the result never contradicts all the files at once.

A step's title is its Normal version's **current** title. A group's label is
set once, when the group forms, and does not follow later renames.

### How learn-first links adjust that order

The merged order above is only a starting point. The final order must also
respect every link that shapes the path -- `accepted` links and those a teacher
`approved` (`services/publishing.py` -> `order_with_links`):

1. A concept can be placed only once every concept it must come after is
   already placed.
2. Among the concepts that can be placed, the one earliest in the merged order
   goes next.
3. Repeat until every concept is placed.

Wherever no link says otherwise, the merged order stands. A link that runs
against it moves concepts. On *Solid, Liquid and Gas*, a teacher approved
"Diagram description for Solid" before "Solid", while the merged order had Solid
first -- so the diagram became step 2 and Solid step 3, and the review screen
reports those two as moved from the lesson files' order. Nothing else changed.

Links cannot contradict each other in a loop: the review screen refuses a change
that would create one. If a loop ever reached this step, the earliest remaining
concept would be placed and the links it could not satisfy reported, never
silently dropped.

The review screen's preview runs both passes on every load with the links
stored so far; a successful publish runs them and saves the result
(`LearningPathStep`).

---

## What decides an edge

An edge says: *understanding A is expected to support understanding B.*

Three criteria vote; vetoes and a section rule can then cancel or downgrade the
result, and a teacher can add, approve or remove edges by hand.

### The three voting criteria

> *Superseded by the 2026-09-17 revision (last section); kept as history.*

**1. Temporal order (TemO)** -- which one the teacher's own material presents
first.

```
TemO(A, B) = 1  if Position(A) < Position(B)
           = 0  otherwise
```

`Position` is the concept's place in the merged order described above. Every
concept counts, including glossary-style sections; excluding those was part of
the original design but is not implemented.

**2. Semantic reference (CSR)** -- whose text is talking about the other, even
without naming it outright.

```
CSR(A, B) = mean over windows j of  cosine( window_j of B's text, name of A )
A is prerequisite of B when CSR(A, B) > CSR(B, A)
```

B's text is cut into 10-word sliding windows, each embedded and compared against
A's concept name, using `all-MiniLM-L6-v2` -- the same model already loaded for
grouping, so nothing new is installed.

**If either concept has no usable name, no CSR vote is cast.** A concept titled
"Examples", or with a sentence for a title, cannot be searched for, so its side
of the comparison is 0 by construction rather than by measurement. Comparing a
real score against it made the named concept win automatically.

**3. Inbound/outbound ratio (IOLR)** -- is this concept foundational (many
things refer to it, it refers to few) or derivative?

```
IOL(x)     = (inbound CSR to x) / (outbound CSR from x)
IOLR(A, B) = 1 if IOL(A) > IOL(B) x 1.25 else 0
```

Free to compute: it is row and column sums of the matrix CSR already built.

- **A real gap is required (25%).** Measured ratios sat between 1.60 and 2.12,
  so a bare `>` let 2.001 beat 2.000 on nearly every pair.
- **Unnamed concepts get no ratio, and no IOLR vote is cast for them** -- the
  same reason as CSR. Before this, an unnamed concept scored 0 and 55 verdicts on
  real content rested on nothing else.

### Things that no longer bypass the vote

**Chunk continuation** is no longer an edge. Split "(Part 1 of 3)" pieces are
merged into one concept before ordering (`concept_units._merge_split_passages`),
so there is no pair left to connect.

**Explicit author statements (S1)** are not implemented in v3. They were
recognised by a fixed list of phrases ("to understand", "should come first"),
the same kind of wording-dependent rule removed below.

---

## The decision

> *Superseded by the 2026-09-17 revision (last section); kept as history.*

```
score(A, B) = (TemO + CSR_direction + IOLR) / 3
```

| score | outcome |
|---|---|
| 3/3 | **accepted** -- the edge constrains the order (unless it crosses sections, below) |
| 2/3 | **pending** -- stored, offered to the teacher as a collapsed suggestion, does *not* constrain the order |
| 1/3 or 0 | discarded |

**Density guard: position alone can never make an edge.** At least one content
vote (CSR or IOLR) is required. TemO fires on one direction of *every* pair --
with 20 chunks that is 190 of 380 ordered pairs before a single word is read. An
earlier design without this guard turned 28% of all possible pairs into edges.

### Vetoes applied after the vote

Two checks can cancel an edge the votes would otherwise allow:

1. **Same concept** -- two concepts owning the same name cannot depend on each
   other.
2. **Every mention is contrastive** -- "a gas spreads out, *unlike* a solid"
   mentions solid, but contrasts it rather than building on it. Contrast is
   scoped to the clause: in "liquids and gases flow, *while* solids do not" only
   solids are contrasted.

> **Known limitation -- contrast uses a fixed word list.** The markers are
> *while, whereas, unlike, but not, although, however*. It misses contrasts
> written without them ("solids do not flow", "different from a solid") and is
> English-only. A model that recognises contrast (NLI) would generalise; it was
> not adopted because it adds a model and slows the build, and the hand-labelled
> check had not yet shown whether contrast causes wrong edges in practice.

### Cross-section edges are never accepted automatically

> *Superseded by the 2026-09-17 revision (last section); kept as history.*

An edge between two concepts that sit under **different lesson headings** (in
the same or different files) is at most **pending**, whatever it scored. A
concept under no heading -- a closing comparison, an opening definition --
crosses nothing.

This uses the documents' own structure, not title wording, so renaming an
object does not change it. It is a downgrade rather than a veto because some
lessons do build section on section (Addition before Multiplication).

**Evidence -- blind hand-check, 2026-09-14** (`docs/learning_path_hand_check_2026-09-14.json`).
62 edges for *Solid, Liquid and Gas* (all 42 accepted, 20 sampled pending),
judged by a teacher who saw both passages but not the rules' verdict:

| rules said | edges | judged correct |
|---|---|---|
| accepted | 42 | 17 (40%) |
| pending, forward | 10 | 4 (40%) |
| pending, pointing back up the path | 10 | 1 (12%) |

| the two concepts sit under | yes | no | unsure |
|---|---|---|---|
| the same heading | 9 | 1 | 2 |
| **different headings** | **0** | **35** | 0 |
| one has no heading | 13 | 2 | 0 |

With the downgrade, 17 of the 18 edges still accepted were judged correct
(94%). **Caveat:** the rule was found in the same answers it is measured on, in
one lesson whose sections happen to be parallel. It needs confirming on a
second topic before the figure is quoted as general.

**Removed checks, and why:**

| check | why it was removed |
|---|---|
| **Siblings** | Keyed on the words of the topic title. Renaming one object to "Diagram description for **Solid**" silently made it a sibling of Solid, Liquid and Gas and deleted 13 edges. A rule whose output depends on title wording does not generalise. |
| **Mutual reference** | Matched concept names literally; fired on 0 of 139 measured pairs. A CSR-based version would have affected 2 of 42 accepted edges. |
| **Author-statement override** | Itself a fixed phrase list, and existed only to override the checks above. |

---

## Edge status

Stored in `ConceptPrerequisite`, between concepts (groups) of one topic.

| status | set by | shapes the order? | shown to the teacher |
|---|---|---|---|
| `accepted` | criteria: 3/3 votes, not cross-section | yes | listed under *What must a student learn before this?* |
| `pending` | criteria: 2/3, or 3/3 but cross-section | no | collapsed, with its passage: *The system thinks … may also need to come before …* |
| `approved` | a teacher added it, or approved a suggestion | yes | listed under *What must a student learn before this?* |
| `rejected` | a teacher removed it, or rejected a suggestion | no | not shown |

`rejected` is stored rather than deleted, so re-deriving at the next publish does
not propose the same pair again. `approved` and `rejected` rows are never
overwritten by the criteria; only `accepted` and `pending` rows are replaced.

---

## Teacher control

On review step 5, each concept shows its full passage and a panel titled
*What must a student learn before this?*, where a teacher can:

- **Add** a concept that must come first, from a list of the topic's other
  concepts -> `approved`.
- **Remove** one (with confirmation) -> `rejected`; it is never suggested again,
  though a teacher can add it back by hand.
- **Review suggestions** -- concepts the criteria think may need to come
  *before* this one -- each shown with its passage and flagged when it sits in a
  different section: *Yes, learn it first* -> `approved`, *No* -> `rejected`.

Each concept's box is outlined by its status: dashed grey when it is linked to
nothing, orange when a link moved it from the lesson files' order. The Graph
view draws the same path as a concept map -- the lesson's headings as branches,
concepts in teaching order -- and highlights what a selected concept needs first.

Rules applied to every change (`services/teacher_links.py`):

- **Loops are refused.** A change that would let a chain of links return to
  where it started is not saved, and the message names the concepts in the loop.
- **Only teachers and admins** may change links.
- **Students see a change only after the next successful publish.** The preview
  re-orders at once; the saved path stays as it was, and the preview reports
  `changed_since_publish` until the topic is published again.

Concepts without a usable name (a sentence, a numbered heading, "Everyday
Examples") never receive derived links, so a teacher adding one by hand is the
only way to connect them.

---

## Decisions taken

**Pending edges do not constrain the path.** An unverified guess should not
shape what a learner is taught. Approving one re-sorts the preview at once; the
screen does not yet show what would move *before* the teacher approves.
Approving can also close a loop, so the loop check runs first and refuses the
change.

**Links win over document order**, and the teacher can change links, so the
teacher -- not the file order -- has the final say on sequence.

**Votes stay binary, and margins are recorded anyway.** Three yes/no votes give
only four possible scores (0, 1/3, 2/3, 1), so "two tuned thresholds" really
means choosing among three cut points rather than turning a dial. Using the CSR
*margin* -- 0.62 vs 0.31 leans hard, 0.52 vs 0.50 is a coin flip, and binary
voting treats those as identical -- would give a continuous, genuinely tunable
score. Not adopted now: 42 labelled pairs cannot support fine tuning, and
equal-weight binary voting is what the cited work did. The margins are stored in
each edge's evidence, so switching later needs no re-derivation.

---

## Where the thresholds come from

> *Superseded by the 2026-09-17 revision (last section); kept as history.*

No public dataset can set them. AL-CPL is `(concept, concept, label)`; the
university-course dataset is `(concept, concept, 13 annotators)`. Neither carries
document positions or source text, so TemO cannot be computed on them, CSR has
nothing to window, and IOLR follows from CSR. All three criteria are
uncomputable on that data, and no K-12 prerequisite dataset exists.

The operating rules are the strictest available: 3/3 accepts, 2/3 pends,
everything else is discarded -- erring toward too few edges rather than too many.

The only labels so far are the blind hand-check above
(`docs/learning_path_hand_check_2026-09-14.json`, 62 concept pairs, one topic).
It is what the cross-section rule and the reported precision rest on. It is too
small to tune thresholds, and a second topic is needed before either figure
generalises. (`docs/prerequisite_labelling_sample.csv` predates the concept-level
path: its 42 pairs are between learning objects of one file, so it does not
label this design.)

---

## Differences from the source criteria document

> *Superseded by the 2026-09-17 revision (last section); kept as history.*

All deliberate:

| source spec | here | why |
|---|---|---|
| CSR **sums** window cosines | **mean** | a sum grows with chunk length; ours run 9 to 80+ words, so long chunks would outrank related ones |
| no density guard | position alone cannot make an edge | TemO votes on every pair; without this the middle band fills with "this paragraph came first" |
| IOLR: bare `>` | requires a 25% gap | measured ratios clustered within 1.60-2.12, so `>` was a coin toss |
| every pair scored | unnamed concepts cast no CSR or IOLR vote | their scores are 0 by construction, not by measurement |
| no vetoes | same-name and contrast vetoes | the votes cannot recognise a pair that must never be an edge |

---

## Not done yet

- **Ordering subtopics or modules.** Paths are per subtopic; their order comes
  from the course outline.
- **Adaptive reordering, mastery tracking, remediation.** The saved path is
  served for that work (`GET /api/learning-path/topics/<id>/published/`, see
  `HANDOFF.md`), but the existing student apps (`adaptive/services.py`,
  `adaptive_portal/services.py`) still walk learning objects in PDF order and
  have not been switched to it.
- **Showing the effect of an approval before it is made.**
- **Confirming the cross-section rule on a second topic.**

## Revision 2026-09-17: RefD key-term reference

Measured on two uploaded lessons against the teacher's gold map
(`fixtures/gold_map_*.json`), the name-to-window SBERT similarity voted
backwards on 7 of 8 missed edges, and the cross-section cap blocked correct
edges such as Solid -> Comparing.

- Semantic reference is now RefD-style: the weighted share of A's key terms
  (its name plus distinctive terms it introduces) used by B's text. SBERT
  matches multi-word names phrased differently (`PHRASE_COSINE`).
- Foundationality is unchanged but reads the new reference scores.
- Structural concepts (Examples) take part in no pair and are ordered last.
- "than" clauses count as contrastive.
- The cross-section cap is removed; `cross_section` is still stored.

Before/after numbers: `docs/learning_path_revision_2026-09-17.md`.
