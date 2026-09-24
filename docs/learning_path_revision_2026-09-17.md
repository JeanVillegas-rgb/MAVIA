# Learning-path criteria revision (2026-09-17)

For the manuscript's revision notes beside §2.6. Gold standard: the teacher's
prerequisite map for two uploaded lessons (`backend/learning_path/fixtures/gold_map_*.json`).

## Before (criteria v3: name-to-window SBERT similarity, cross-section cap)

| Topic | Required accepted | Forbidden accepted | Order matches |
|---|---|---|---|
| 62 Solid, Liquid and Gas | 0 / 10 | 1 | yes |
| 79 Reproduction Among Flowering Plants | 1 / 8 | 0 | yes |

## After (RefD key-term reference, Examples structural, no cross-section cap)

Constants: REF_MAX_DF_RATIO = 0.34, REF_MARGIN = 0.0, PHRASE_COSINE = 0.80, MIN_IOL_MARGIN = 0.25

Only `REF_MARGIN` moved from its previous default (0.05 -> 0.0); the grid
(`evaluate_gold_paths --grid`) confirmed the other three at their existing
values -- no row in the top 15 (sorted by forbidden ascending, then required
hits descending, then orders matching) beat them.

| Topic | Required accepted | Forbidden accepted | Order matches |
|---|---|---|---|
| 62 Solid, Liquid and Gas | 10 / 10 | 0 | yes |
| 79 Reproduction Among Flowering Plants | 4 / 8 | 0 | yes |

Extra accepted edges (not required, not forbidden):
- Topic 62: matter->changing, matter->comparing
- Topic 79: reproduction->pollination

## Known gaps

Two calibration runs stopped short of the full gold standard. The first, at
the defaults, reached 6/10 on topic 62 and 0/8 on topic 79. The second, after
the Task 7b amendment below, reached 9/10 and 4/8 at the defaults; its grid
found no row with 0 forbidden edges above 14 of the 18 required edges, and the
best such row differed from the defaults only in `REF_MARGIN` (the values above). The user decided to stop tuning here and accept this result
rather than add lesson-specific rules, word lists, or thresholds tuned to a
single edge. Four required edges on topic 79 (Reproduction Among Flowering
Plants) are not accepted under any grid row that keeps 0 forbidden edges, and
are recorded as `known_missing` in `gold_map_79.json` / `gold_topic_79.json`
rather than silently dropped from the test assertions:

- **Stamen -> Pollination** and **Pistil -> Pollination** come out **pending**
  (teacher-approvable): shared part-terms ("anther", "stigma", "pollen")
  exceed the distinctiveness limit (`REF_MAX_DF_RATIO`), because the
  Reproduction overview section also uses them, pushing their document
  frequency past the cap that would let them count as key terms.
- **Pollination -> Fertilization** and **Fertilization -> Seed** are **not
  proposed at all**. The same shared-part-terms cause applies to
  Pollination -> Fertilization. Fertilization -> Seed additionally fails
  because Seed's text says "fertilized ovule", not "fertilization", and
  `singular()` does not unify "fertilized" with "fertilization" -- fixing that
  would need stemming or lemmatization, a logic change outside this task's
  scope.

The amendment landed in Task 7b (head-word mention and section containment as
reference, `criteria.py` `head_words`/`contained_in`, spec §6) closed 7 of the
original 11 missing edges (6/10 -> 10/10 on topic 62, 0/8 -> 4/8 on topic 79)
before calibration; the remaining four are the ones above.

## Live re-run (2026-09-18)

Both topics were merged by the teacher in the UI and republished, then the
derived paths were compared with the gold standard.

| Topic | Required accepted | Forbidden accepted | Order matches |
|---|---|---|---|
| 62 Solid, Liquid and Gas | 10 / 10 | 0 | yes |
| 79 Reproduction Among Flowering Plants | 4 / 8 | 0 | yes |

Live matches the fixture-based measurement exactly, including which four edges
are missing (the documented known gaps in topic 79). Extra accepted edges:
topic 62 `matter -> comparing`, `matter -> changing`; topic 79
`reproduction -> pollination`. All three are transitive or plausible, none
forbidden.

Merges the teacher applied on topic 62: the four comparison items with the
comparison section and its table; Solid, Liquid and Gas each with the other
file's section, diagram and examples; both Everyday Examples sections; the
particle figure with Matter; "Changing From One State to Another" with its
figure; the two-part Matter introduction. On topic 79: the flower figure with
the introduction in each file. 23 concepts became 7 (topic 62) and 10 became 9
(topic 79).

Three defects surfaced during the run and were fixed before the comparison
above (`object_merge.py`, plus two UI bugs):

1. A merged concept took its title from the first member, so a figure with no
   heading named the concept ("Okay, let's describe this figure for the
   student"). The concept then had no usable name and lost its edges. The title
   now comes from the first heading among the members. Measured before the fix:
   7/10 and 1/8.
2. A merged row kept a stale "taught through" marker, so the versions screen
   refused to generate its missing versions.
3. Resetting the group's version selection discarded a locked label, and the
   group label kept the pre-merge title.

Rows merged before the fix were repaired in place from their stored snapshots,
and both paths were re-derived.


## Live re-run (2026-09-21) — first comparison on a real publish

Everything above was measured against the fixtures. This is the first time
both topics were **actually published from freshly uploaded PDFs** and the
saved `LearningPathStep` / `ConceptPrerequisite` rows were scored against the
gold maps. Topic ids differ from the fixtures (152 and 169, not 62 and 79)
because the lessons were re-uploaded; concepts were matched to gold keys by
name.

### Topic 169 — Reproduction Among Flowering Plants

| | fixture | live publish |
|---|---|---|
| Required edges accepted | 4/8 | **4/8** |
| Gaps | the four `known_missing` | **the same four, no new ones** |
| Forbidden edges | 0 | **0** |
| Order | matches | **matches** |

Reproduced exactly, on a different upload of the lesson, with a different PDF
pair. The two pending gaps (stamen→pollination, pistil→pollination) and the two
unproposed ones (pollination→fertilization, fertilization→seed) are the same
four recorded in `gold_topic_79`'s `known_missing`. This is the first evidence
that the criteria hold on data they were not fitted to chunk-for-chunk.

### Topic 152 — Solid, Liquid and Gas

Measured twice, because the first publish happened before the teacher had
answered the grouping review queue.

| | fixture | publish #1 (queue unanswered) | publish #2 (queue answered) |
|---|---|---|---|
| Concepts | 7 | 20 | 12 |
| Required edges accepted | 10/10 | 6/10 | **9/10** |
| Forbidden edges | 0 | 0 | **1** |
| Order | matches | matches | **matches** |

**Publish #1.** The four missing edges (solid/liquid/gas → comparing, and
comparing → changing) were all *pending*, never absent. The stored evidence
shows why:

```
Solid → Comparing the Three States
  temporal_order 1 · semantic_reference 1 · inbound_outbound 0
  ref_forward 0.52
```

Two criteria of three fired strongly; only foundationality failed. The
"Comparing the Three States" concept had fragmented into six concepts — a
24-word caption, the table figure, and Shape / Volume / Particle arrangement /
Flow standing alone — so the inbound weight that makes one concept
foundational was spread six ways and no fragment cleared `MIN_IOL_MARGIN`.

This was not a criteria failure. The grouping step had raised a review card
(score 0.384, between the 0.3 review and 0.6 auto thresholds) pairing exactly
`[Shape, Volume, Particle arrangement, Flow]` with `[caption, table figure]` —
which is precisely gold's `comparing` member set. It asked instead of guessing,
and the question had not been answered yet.

**Publish #2.** After the teacher accepted that card (and three others, and
rejected one), 20 concepts became 12, and required edges went 6/10 → **9/10**
with the order still exact. The grouping card was worth four edges.

### The one forbidden edge, and what causes it

Publish #2 accepted `Solid → Gas`. Gold declares solid, liquid and gas
**parallel**: no edge between them is correct, and a learner would be told to
master Solid before Gas.

```
Solid → Gas   [accepted]
  temporal_order 1 · semantic_reference 1 · inbound_outbound 1
  ref_forward 0.0346
  terms_forward: ["compress", "dot", "drawn", "spaced"]
```

Those four "distinctive" terms are not science. They come from the two
`Diagram description` objects now bundled into the concepts:

- Solid: *"particles in a solid are **drawn** as evenly **spaced dots** ..."*
- Gas: *"particles in a gas are **drawn** as widely **spaced dots** ..."*

Both figure descriptions were written by the same vision model in the same
house style, so they share rendering vocabulary. The RefD key-term criterion
assigns those terms to the earliest concept that uses them (Solid), then sees
Gas "using" them and votes reference = 1 — at `ref_forward` 0.0346, which is
noise. `REF_MARGIN = 0.0` lets any positive margin through.

This is the **same stock-figure-phrasing problem already documented for
grouping** (two AI figure descriptions scoring 0.6+ against each other
regardless of subject), reappearing inside the learning-path criteria.

Three things follow, and none of them is "tune the constants":

1. A blunt minimum on `ref_forward` would not work. Legitimate accepted edges
   in this same run sit just as low — `Matter → Comparing` at 0.0257,
   `Gas → Changing` at 0.0207. A floor that killed 0.0346 would kill those.
2. The principled fix is to stop **rendering** vocabulary ("drawn", "dots",
   "spaced", "shown", "illustrates", "diagram") from counting as distinctive
   key terms. It describes the medium, not the science. This is the same
   reasoning as `REF_MAX_DF_RATIO`, which did not catch it here because only
   2 of 12 concepts carry a diagram — too few to look common.
3. **The gold test does not catch this.** `gold_topic_62.json` holds the older
   upload's text, which has no such figure descriptions, so
   `test_gold_paths.py` stays green while live output carries a forbidden
   edge. The fixtures are not a safety net for text the extraction produces
   today.

### Remaining gap on topic 152

`comparing → changing` is the last required edge, and it is now *absent*
rather than pending. "Changing From One State to Another" is still two
concepts — a caption object and its figure, both from the same PDF, with no
partner in the other PDF to corroborate them, so no grouping card was raised.
Joining them is a manual **Move to…** in the review screen.

### Status

- Audio: 31/31 playlist clips and every generated version present on disk,
  non-zero, no empty narration, across both topics.
- Topic 169: matches the gold standard exactly.
- Topic 152: order exact, 9/10 required edges, **1 forbidden edge outstanding**.
- Figure descriptions have still not been regenerated. That open item is no
  longer cosmetic: it is the direct cause of the forbidden edge above.

## Revision (2026-09-22) — the evidence bar for an edge between parallel concepts

The open item from the section above: `Solid → Gas` on topic 152, accepted on
four words of diagram vocabulary. The candidate fix recorded there was to stop
non-technical and rendering vocabulary counting as distinctive. **That direction
was tried and measured, and it does not survive the gold standard.** What
shipped instead is a structural rule. Both measurements are below.

### The live numbers first, re-derived before anything was changed

Topics 152 and 163 were re-scored against the gold maps by re-running
`concepts_for_topic` + `criteria.decide_pairs` — which is exactly what
`publish_learning_path` does, so these are the numbers a republish would store.
The stored rows were stale (they predate a concept rename); nothing was written.

| | 152 | 163 | 169 |
|---|---|---|---|
| Concepts | 14 | 18 | 10 |
| Required edges accepted | 9/10 | *no gold map* | 4/8 |
| New gaps (not recorded) | 1 (`comparing → changing`) | — | 0 |
| Forbidden edges | **1** (`solid → gas`) | **20 of 41 cross lessons** | 0 |
| Order matches | yes | no | yes |

Topic 169 still reproduces the gold standard exactly. Topic 152's one remaining
gap is `comparing → changing`, and it is *not* a criteria failure: the pipeline
split each of those concepts in two (objects 318/319 and 331/403), the two
halves carry the same name, and the same-name veto correctly refuses an edge
between them. Joining them is a **Move into another concept…** in the review
screen.

Across the three topics, 53–62% of accepted edges rested on two terms or fewer
(topic 163: 33 of 41).

### Topic 163 is three lessons, and that is most of its problem

Every criterion assumes a topic *is* a lesson. Topic 163's three PDFs are the
Skeletal, Digestive and Circulatory systems. Re-deriving each PDF's concepts as
its **own topic**:

| | as one topic | as three topics |
|---|---|---|
| Accepted edges | 41 | **22** |
| Crossing lessons | 20 | **0, by construction** |

The 22 that remain are all plausible — each system → its own parts by section
containment, `support → spine`, `small intestine → large intestine` — and the
thinnest of them are carried by real science (`stomach → large intestine` on
"enzyme", `mouth → large intestine` on "chew"). **So topic 163 should be three
topics.** Splitting it removes 20 of its 21 wrong edges with no code at all,
which is the measure of how much of that topic's failure is genuinely the
criteria: very little.

The two cross-lesson edges that a structural fix cannot reach are
`blood cell production → circulatory system` (head word "blood") and
`small intestine → spine` (head word **"small"**, an ordinary adjective
granting a full name-weight reference at 0.5). Head words are a separate weak
point, recorded here and not addressed.

### The vocabulary filter: measured, and ruled out

A filter was built to the principle this document states — words describing the
medium or the prose rather than the science ("drawn", "dots", "spaced",
"shown", "diagram", "explain", "outline", "lesson", "person", "way", "strong",
numerals, and kin) — deliberately *not* curated to spare any one edge. Applied
to `key_terms`:

| | gold 62 | gold 79 |
|---|---|---|
| Required accepted | **9/10** (was 10/10) | 4/8 |
| Edge lost | **`comparing → changing`** | — |
| Forbidden | 0 | 0 |

Gold topic 62's required `comparing → changing` is itself carried by
`["explain", "four", "outline"]` at `ref_forward` 0.0441 — no name, no head
word, no section containment. It is the *same evidence class* as the four words
carrying `solid → gas`. **Any vocabulary filter honest enough to catch "drawn"
and "spaced" also catches "explain" and "outline", and takes topic 62 off
10/10.** The direction cannot be reconciled with the acceptance test, so it was
abandoned rather than curated into a list that spares one edge — which is the
lesson-specific word list the 2026-09-17 calibration decided not to write.

This also settles the thresholds question for good. Sorted by `ref_forward`:
`gas → changing` 0.0206 (required), `solid → gas` 0.0369 (forbidden),
`comparing → changing` 0.0441 (required). By number of carrying terms: 1
(required), 4 (forbidden), 3 (required). Neither quantity separates right from
wrong in either direction.

### What shipped: coordinate siblings need a reference that names their target

The signal that *does* separate them is structural, and it is the one Wang et
al. (2016) already supply. `contained_in` reads section structure downward — a
passage under the "Matter" heading builds on Matter. The sideways reading of
the same structure is that two passages under **one** heading, neither of which
is what that heading names, are **coordinate siblings**: Solid, Liquid and Gas
under "Matter"; Support, Protection and Movement under "The Skeletal System".
A learner does not master Solid before Gas.

Between two siblings, an edge now needs a reference that *names* its target —
the target's name, a head-word mention, or section containment — rather than
merely sharing vocabulary with it. Parallel passages share vocabulary by
construction, because the author describes each of them the same way; that is
what "drawn as evenly spaced dots" and "drawn as widely spaced dots" are.

The concept a shared heading names is the parent, not a sibling
(`Matter → Solid` is untouched), and a concept under no heading is nobody's
sibling — the same rule `crosses_sections` follows. It keys on the documents'
own headings, never on the words of a title, which is what made the *removed*
sibling rule ungeneralisable.

`criteria.presented_in_parallel`, `criteria.names_the_target`. No constant
moved.

| | gold 62 | gold 79 | live 152 | live 169 | live 163 |
|---|---|---|---|---|---|
| Required accepted | 10/10 | 4/8 | 9/10 | 4/8 | — |
| New gaps | 0 | 0 | 0 | 0 | — |
| Gaps closed | 0 | 0 | 0 | 0 | — |
| Forbidden accepted | 0 | 0 | **0** (was 1) | 0 | — |
| Order matches | yes | yes | yes | yes | — |
| Accepted total | — | — | 19 (was 20) | 8 | 39 (was 41) |

`Solid → Gas` is gone. Nothing else changed on either gold topic, and topic 169
still reproduces the gold standard exactly. On topic 163 the rule prunes two
within-system edges and, as expected, none of the 20 that cross lessons —
those concepts come from different PDFs and share no heading. **Topic 163 needs
the split, not the criteria.**

### A third gold fixture, built from the text the extraction produces today

The blind spot recorded above — "the fixtures are not a safety net for text the
extraction produces today" — was the reason this defect could sit in live output
while `test_gold_paths` stayed green. It is now closed, and closing it turned
out to matter more than the fix.

A first attempt exported topic 152 through `export_gold_concepts`, which writes
the **teacher's** grouping: 7 concepts, today's text. It scores 8/10 with **0
forbidden edges** — it cannot see `Solid → Gas` at all. The reason is the
denominator. `REF_MAX_DF_RATIO` allows a term used by at most
`floor(n × 0.34)` concepts; the diagram vocabulary sits in 3 concepts either
way, so at n = 7 the cap is 2 and the words are dropped, while at n = 14 the cap
is 4 and they survive. **The teacher's ideal grouping hides the failure the
teacher sees.** (That attempt also loses `matter → solid`, because today's
Matter concept absorbs two passages that talk at length about solids and gases,
which costs it its foundationality vote.)

So the new fixture freezes the shape a publish actually derives.
`export_live_concepts` (new) writes `concepts_for_topic` output — the
pipeline's own concepts, members, headings and order — and labels each with the
teacher's concept it belongs to. Two things the older fixtures could not
express:

- **Several concepts may carry one key**, because the pipeline split a concept
  the teacher keeps whole. An edge between two concepts sharing a key is a
  grouping result, not a prerequisite claim, and is not scored.
- **A concept may carry no key.** Three of topic 152's fourteen are not in the
  teacher's map. They still take part in the derivation — they change document
  frequencies and the order — but no edge is scored against them.

`gold_map_152.json` / `gold_topic_152.json`, asserted by
`test_gold_paths.test_solid_liquid_and_gas_as_the_pipeline_groups_it_today`.
Confirmed to **fail before the fix** on `forbidden_accepted: [["solid","gas"]]`
and pass after. `comparing → changing` is recorded as its one `known_missing`,
with the same-name veto as the reason.

### Still open

- **Topic 163 should be three topics.** Measured above: 41 accepted edges → 22,
  and 20 wrong ones vanish. No code.
- **Topic 152's two split concepts** (objects 318/319, and 331/403) need
  **Move into another concept…**. Joining 318/319 is what would close
  `comparing → changing`; when it does, `known_missing` in `gold_map_152.json`
  must be emptied and the fixture re-exported, or the test will fail on a
  closed gap.
- **Head words can be ordinary adjectives.** "small intestine" lends "small" a
  full name-weight reference. Not addressed.
- **A fixture is a snapshot.** `gold_topic_152.json` holds the 2026-09-21
  upload. Re-export it with `export_live_concepts` after a re-upload, and say
  in the log that the numbers moved because the fixture moved.
