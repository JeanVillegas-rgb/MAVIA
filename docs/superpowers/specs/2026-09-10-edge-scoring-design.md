# Edge scoring: multi-criteria voting with reference asymmetry

Design date: 2026-09-10. Component: `backend/learning_path/services/edge_derivation.py`.

## Why change what exists

Edges are currently created by five independent signals, each writing its own
row with a hand-picked weight (1.0, 0.6, 0.4, 0.8, 1.0). Three problems:

1. **The weights are unbacked numbers.** They were chosen by judgement, like the
   `0.60` semantic grouping threshold. Each one is something a reader can ask us
   to justify and we cannot.
2. **A single mention is enough to create an edge.** If passage B names a
   concept passage A defines, an edge is drawn — without checking whether A also
   names B's concept. A mutual mention is not evidence of dependency in either
   direction, but the current rule records one anyway.
3. **Two of the five signals are our own invention**, not drawn from the
   prerequisite-relation literature.

## What the literature supports

Three criteria carry research backing:

- **Reference direction.** Liang et al. (2015) introduced *RefD* (Reference
  Distance), which infers prerequisites from asymmetric referencing: articles
  about calculus refer to algebra, articles about algebra rarely refer to
  calculus, therefore algebra precedes calculus. It is a widely cited baseline.
- **Temporal / first-mention order.** Used throughout as an ordering feature —
  `order_diff` in PRELEARN systems, "the order in which the concept appears in
  the course structure" in MOOC work.
- **Co-occurrence.** A standard text-based feature, normally used as one
  criterion among several rather than on its own.

For combining them **without labelled training data**, the supported method is
multi-criteria voting: evaluate each criterion in both directions, count the
votes, normalise the difference, and use a threshold band where the middle means
*no relationship*. Alatrash, Chatti, Wibowo and Ul Ain (2025) apply exactly this over ten
criteria with equal weights and no training data, tuning each criterion for
precision over quantity.

MAVIA has no labelled prerequisite pairs and no plausible way to get them at
this scale, so voting is the defensible choice.

## Design

### Orientation and existence are separate decisions

**Document order decides direction. Evidence decides whether an edge exists.**

An edge may only run forward through the material, as today. A passage using a
word the material does not define until later is using it loosely, not depending
on it. Keeping this as a hard rule — rather than folding it into the vote —
preserves the property that the edge set is a subgraph of a strict total order,
and therefore acyclic. Kahn's algorithm still verifies rather than trusting it.

This also matches how the component already describes itself: the document
"informs the graph but does not dictate the path", and its sequence is used to
orient an edge and as the lowest-priority tie-breaker.

### The four voting criteria

Each is evaluated in **both** directions for a candidate pair (A earlier, B
later) and contributes at most one vote.

**C1 — Body reference asymmetry (RefD principle).**
`refs(B→A)` is true when B's content mentions a concept term drawn from A's
title; `refs(A→B)` likewise. The criterion votes for A→B only when `refs(B→A)`
is true **and** `refs(A→B)` is false. Mutual mentions and mutual silence both
produce no vote.

This is the substantive change from today, where `refs(B→A)` alone was enough.

**C2 — Section reference asymmetry.**
The same test against section headings rather than body text. A heading mention
is weaker evidence than a body mention, which under equal-weight voting is
expressed by it being a separate criterion that can fail independently, rather
than by a smaller weight.

**C3 — Distinctive term co-occurrence.**
A and B share at least `MIN_SHARED_TERMS_FOR_COOCCURRENCE` (3) terms whose
document frequency is at or below the distinctive limit (15% of passages). This
criterion is symmetric by nature, so it votes in the direction document order
already established, and never against it.

Thresholds inside each criterion are unchanged; they are operating values, not
calibrated ones, and are documented as such.

### Scoring

```
votes_forward  = number of criteria voting A → B
votes_backward = number of criteria voting B → A
score = (votes_forward - votes_backward) / 4        # in [-1, 1]
```

An edge is created when `score >= VOTE_THRESHOLD`. The default is **0.25**, one
net vote out of four. Raising it to 0.5 requires two net votes and is the
precision-first setting; it is a single constant, documented as a chosen
operating value.

`PrerequisiteEdge.weight` stores `score` rather than a per-signal constant. The
number in the database then means something explainable: how much of the
available evidence pointed this way.

### Two edges that bypass the vote

**Chunk continuation.** `"(Part 1 of 3)"` and `"(Part 2 of 3)"` are one passage
the chunker had to cut. That is not evidence about prerequisites — it is a fact
about our own preprocessing, and it is the temporal-order criterion applied to a
chunker artifact. Voting on it would allow the halves of one explanation to be
scattered across the path whenever the evidence happened to be thin. It is kept
as a mandatory edge with weight 1.0, exempt from voting and from the co-definer
rule, and described in writing as document order rather than as a discovered
relation.

**Teacher-authored edges.** Unchanged: weight 1.0, never overwritten by
re-derivation. A person who knows the subject outranks every criterion here.

**C4 — Definition scope.** The material's opening definition votes for every
later definition. Added back during implementation after measuring the cost of
removing it: on the Grade 1 Science course it left 8 of 10 passages in one PDF
as unconnected roots, because "A solid has a definite shape" never says the word
"matter" and so no reference criterion can reach it.

The relation it reaches for — a general concept containing the specific ones
that follow — is recognised in the literature as **category containment**, which
the multi-criteria work detects from an external knowledge base. MAVIA has none,
so this is a local proxy for a legitimate criterion, and is described as one.
That is why it is worth exactly one vote rather than the 0.8 weight it used to
carry. It only ever votes forward, since the opening definition is by
construction the earliest.

### What is removed

Nothing is deleted outright. `TITLE_REFERENCE`, `SECTION_REFERENCE`,
`TERM_COOCCURRENCE` and `DEFINITION_SCOPE` stop being *signals* with weights of
their own and become *criteria* that vote.

The co-definer rule is retained: no edge is drawn between two takes on the same
concept, chunk continuation excepted.

## Model changes

`PrerequisiteEdge.Signal` reduces to three values:

| Value | Meaning |
|---|---|
| `VOTED` | Created by the criteria vote; `evidence` records which criteria fired |
| `CHUNK_CONTINUATION` | Structural, from the chunker's part markers |
| `TEACHER_AUTHORED` | Added by a person during review |

The four criteria are recorded inside `evidence` instead. One pair therefore
yields **one** row rather than one row per signal.

`evidence` for a voted edge records, at minimum: the criteria that voted each
way, the score, the threshold in force, and the terms behind C1/C2/C3 — so a
teacher reviewing the graph can see why an edge exists.

The table currently holds **zero** rows, so no data migration is required. The
unique constraint `(prerequisite, dependent, signal)` still holds.

## Consumers

No changes needed downstream, verified against the current code:

- `path_builder._edges_and_weights` already collapses several rows per pair by
  taking `max(weight)`. With one row per pair that becomes a no-op.
- `weight` feeds `mean_incoming_confidence`, which is a tie-breaker in the sort.
  Voted scores fall in `[0.25, 1.0]`, a comparable range to the previous
  `[0.4, 1.0]`, so ordering behaviour does not shift meaningfully.
- `signal` is passed through for display only.

## Derivation scope

Derivation must run over **teaching steps only** — learning objects with
`represented_by IS NULL`. Content versions introduced representation after this
component was written, so it currently has no knowledge of it and would emit
edges for objects the lesson no longer teaches. Four objects are represented in
the current course.

Edges are derived per material, as today. Cross-PDF relationships are handled by
semantic grouping, not here, which is why the co-definer rule exists.

## Testing

- **C1 asymmetry:** one-way mention votes; mutual mention does not; mutual
  silence does not.
- **C2:** heading mention votes independently of body mention.
- **C3:** three distinctive shared terms vote; three common terms do not.
- **Scoring:** one net vote reaches the default threshold, zero does not, and a
  score at exactly the threshold creates an edge.
- **Orientation:** no edge is ever created against document order.
- **Chunk continuation:** created even when no criterion votes, and between
  co-definers.
- **Teacher edges:** survive re-derivation.
- **Scope:** represented objects produce no edges.
- **Acyclicity:** the derived graph passes Kahn's on the real course.

## Honest limitations

**This is RefD's principle, not RefD.** The published metric measures Wikipedia
hyperlinks between articles and computes a ratio over linked concepts. MAVIA has
no hyperlinks; C1 tests whether one passage's text mentions another's concept
term. Describe it as reference asymmetry adapted to passage-level mentions —
claiming an implementation of RefD would not survive scrutiny.

**`VOTE_THRESHOLD` is still a chosen number.** Voting removes four hand-picked
weights and replaces them with one hand-picked threshold. That is a real
reduction, not an elimination. The comparable published value is 0.28 over ten
criteria, tuned empirically for that domain.

**Nothing here is evaluated.** No labelled prerequisite pairs exist for this
content, so the change is justified by method rather than by measured accuracy.
The graph remains teacher-correctable, which is what carries the risk.

## Sources

- Liang, Wu, Huang, Giles (2015), *Measuring Prerequisite Relations Among
  Concepts* — the RefD metric.
  https://www.researchgate.net/publication/301445972_Measuring_Prerequisite_Relations_Among_Concepts
- Alatrash, Chatti, Wibowo, Ul Ain (2025), *Inferring Prerequisite Knowledge
  Concepts in Educational Knowledge Graphs: A Multi-criteria Approach* — the
  unsupervised voting method. https://arxiv.org/abs/2509.05393
- Wang, Ororbia, Wu, Williams, Giles (2016), *Using Prerequisites to Extract
  Concept Maps from Textbooks*. https://dl.acm.org/doi/10.1145/2983323.2983725
- *NLP-CIC @ PRELEARN: Mastering prerequisites relations, from handcrafted
  features to embeddings* — order_diff as an ordering feature.
  https://arxiv.org/pdf/2011.03760
- *Prerequisite Relation Learning: A Survey and Outlook*, ACM Computing Surveys.
  https://dl.acm.org/doi/10.1145/3733593
