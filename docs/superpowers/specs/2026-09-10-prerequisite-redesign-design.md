# Prerequisite inference: redesign

Design date: 2026-09-10. Replaces the four-criteria vote in
`backend/learning_path/services/edge_derivation.py`.

Scope: the generic, learner-independent prerequisite graph. No reinforcement
learning, no learner state.

---

## A. Diagnosis of the current approach

### The numbers

| | |
|---|---|
| 10-node lesson | 10 edges |
| 55-node lesson | **416 edges** |
| edges scoring 0.25 (one criterion) | **93%** |
| edges resting on `body_reference` alone | **88%** |
| forward pairs available in the 55-node lesson | 1,485 |
| **share of all possible pairs that became edges** | **28%** |

### What they imply

**1. The system is measuring topical association, and association is dense.**
In one lesson about one subject, "passage B contains a word from passage A's
title" is true for roughly a quarter of all ordered pairs. It is nearly a
tautology: a lesson about matter mentions matter throughout. Prerequisite
relations are sparse — a concept genuinely depends on one or two prior
concepts, not fifteen. A detector whose base rate is 28% cannot output a sparse
relation no matter where the threshold sits.

**2. The vote never actually votes.** 93% of edges carry one criterion. The
design was presented as multi-criteria corroboration; in practice it is a
single criterion with three that rarely fire. The `/4` denominator makes the
scores *look* discriminating (0.25 vs 1.0) while 385 of 416 edges sit at the
same value. The formula reports a precision it does not have.

**3. Candidate generation and evidence evaluation were the same step.** Every
forward pair was a candidate, and any one signal promoted it. So graph density
is set by the *weakest* signal in the ensemble. Adding a criterion could only
ever add edges; none could remove one except through the rarely-triggered
backward vote.

**4. Raising the threshold does not fix the concept.** At 0.5, 385 of 416 edges
disappear, leaving ~31 — but they would be the pairs where two *association*
signals happened to agree, not the pairs that are pedagogically dependent.
Higher precision on the wrong target.

**5. `section_reference` never fired because the data is not there.** Only
**11 of 67** chunks have a non-empty `section_title`. That is an extraction
gap, not a logic bug, and no amount of redesign at this layer fixes it.

### The root cause in one sentence

The current system asks *"are these two passages related?"* and calls the answer
a prerequisite. The question it needs to ask is *"is understanding A reasonably
necessary for understanding B?"* — which is a different, and much rarer,
relation.

---

## B. New architecture

```
PDF
 ↓
content extraction            ← section hierarchy must be captured here (see A.5)
 ↓
learning objects (chunks)
 ↓
[1] CONCEPT RESOLUTION        which concept does each chunk own?
 ↓
[2] CANDIDATE GENERATION      narrow the pair space (recall-oriented, cheap)
 ↓
[3] RELATION TYPING           what KIND of relation is this? (is-a, example, …)
 ↓
[4] SUPPRESSION               siblings, co-definers, contrastive mentions
 ↓
[5] PREREQUISITE DECISION     evidence tiers; only some types qualify
 ↓
[6] GRAPH CONSTRUCTION        acyclicity check, cycle breaking
 ↓
prerequisite DAG              what must precede what
 ↓
[7] SEQUENCE BUILDER          + author's presentation order
 ↓
generic learning path
```

**A prerequisite graph is not a learning path.** `Matter → {Solid, Liquid, Gas}`
says nothing about the order of the three states, because they are siblings and
no dependency holds between them. Something still has to choose. Making the
graph carry that decision is what forced the old design to mislabel sequencing
as dependency.

The critical change from the current design is that **[2] and [5] are
separate**. Candidate generation may be generous; the prerequisite decision is
strict. Density is then governed by the strictest stage, not the loosest.

---

## C. Evidence types

Notation: `A → B` means A is a prerequisite of B.

### STRONG — may create an edge alone

**S1 · Explicit dependency statement.**
*Detects:* the author saying, in words, that one thing must be understood
before another.
*Why it counts:* it is the curriculum author's own pedagogical judgement, which
outranks any inference we could make.
*Example (real, from your material):* "Heating and cooling can cause changes of
state, **but understanding each state should come first**." → `Solid → Changes
of State`, `Liquid → …`, `Gas → …`.
*Independent:* yes.

**S2 · Taxonomic is-a, where the parent is taught in the same material.**
*Detects:* `B is a <kind|type|form|state|category> of A`, where the sentence
subject is B's own concept **and** A is itself a learning object in this
material.
*Why the condition matters:* taxonomic dependency is not automatically
instructional dependency. "A whale is a mammal" is ontologically true, but a
Grade 1 learner does not need the general concept of mammals before learning
about whales. What makes `Matter → Solid` different is that the author *teaches
matter*, in this lesson, and then frames solid as one of its states. That is
instructional framing, not an ontology lookup.
*Where the parent is not taught here*, the relation is recorded as MEDIUM and
needs corroboration — which in practice usually means no edge, correctly.
*Example (real):* "A solid **is a state of matter** that has a definite shape."
→ `Matter → Solid`, because `Matter` is a chunk in the same material.
*Independent:* yes, subject to the condition above.
*Caveat:* the subject check is essential. Without it, "there **is a** lot of
space between particles" extracts `Gas is-a space`. That false positive occurred
in the simulation.
*Double-counting guard:* when the is-a sentence is also B's defining sentence,
S2 and M1 are the same observation. Only S2 counts.

### MEDIUM — two of *different* types required

**M1 · Definitional dependency.**
*Detects:* A's concept term appears **inside B's defining sentence** — not
anywhere in B.
*Why:* using a term to define something is different from mentioning it later.
*Example:* "A solid is matter with a definite shape" → `Matter → Solid`.
*Independent:* no.

**M2 · Aggregation chunk.**
*Detects:* B is a comparison/summary chunk (its title or section says so), and A
is one of the concepts it treats **non-contrastively**.
*Why:* a chunk whose purpose is comparing three states cannot be understood
without them.
*Example:* "Comparing the Three States" → requires `Solid`, `Liquid`, `Gas`.
*Independent:* no.

**M3 · Instantiation.**
*Detects:* B presents an instance of A — `<instance> is a/an <A-concept>`.
*Why:* an example of a solid is unintelligible without the concept of a solid.
*Example:* "An ice cube is a solid" → `Solid → Everyday Examples`.
*Independent:* no.

**M4 · Section progression.**
*Detects:* A and B share a section, A introduces the concept and B explains or
applies it.
*Why:* an author placing an explanation after an introduction inside one section
is expressing an instructional order.
*Independent:* no.
*Currently unusable:* only 11 of 67 chunks carry a section title.

### WEAK — never creates an edge

**W1** body mention anywhere · **W2** distinctive co-occurrence · **W3** document
adjacency.

These serve **candidate generation and explanation only**. They are recorded in
evidence so a teacher can see why a pair was considered, and they never
contribute to acceptance. This is the single biggest change: `body_reference`,
which currently produces 88% of edges, is demoted to a candidate generator.

### SUPPRESSORS — block an edge unless S1 overrides

These express strong negative evidence, not logical impossibility. An explicit
dependency statement (S1) is the author speaking directly, and overrides all of
them: sibling concepts *can* be sequenced deliberately ("single-digit addition"
before "multi-digit addition" share a parent yet one grounds the other), and a
mutual reference makes direction ambiguous rather than proving no dependency
exists. V2 is the one exception that stays absolute — two chunks about the same
concept cannot be prerequisites of one another.

**V1 · Siblings.** Two concepts sharing an is-a parent, or occupying parallel
positions in one section, are peers. Never link them.
*Effect:* kills `Solid → Liquid → Gas`. Verified: Solid, Liquid and Gas all
resolve to parent `matter` in your material.

**V2 · Co-definers.** Two chunks claiming the same concept (two PDFs both
titled "Solid", or "(Part 1 of 3)" splits). Retained from the current design.

**V3 · Contrastive mention.** A mention inside a contrastive clause — *while,
whereas, unlike, but not, although* — is not evidence of dependency.
*Effect:* kills `Solid → Flow`, since Flow says "Liquids and gases can flow,
**while** solids normally do not." `Liquid → Flow` and `Gas → Flow` survive,
because there liquids and gases are the subject.

**V4 · Mutual reference.** If each names the other, the reference establishes
relatedness, not direction. Retained from the current design.

---

## D. Scoring and rules

No numeric weights. There is no data with which to justify them, and inventing
them is what made the current scores look more meaningful than they were.
Acceptance is **ordinal**:

```python
def accept(evidence, suppressors):
    if suppressors:                      # V1-V4 are absolute
        return False
    if evidence.strong:                  # S1 or S2
        return True
    if len({e.type for e in evidence.medium}) >= 2:
        return True                      # two DIFFERENT medium types
    return False                         # weak evidence never accepts
```

Two medium signals of the *same* type do not corroborate — two instantiation
matches are one kind of observation seen twice.

**The two-medium rule is an unvalidated operating decision, not a derived
result.** There is no theoretical reason why M1+M3 constitutes a prerequisite
while M1 alone does not. It is chosen to favour precision, and it must be
described that way in writing — e.g. *"candidate edges lacking direct
prerequisite evidence are accepted only when corroborated by at least two
distinct indirect indicators; this conservative criterion was selected to reduce
false-positive prerequisite relationships and is evaluated through expert
review."* Its advantage over numeric weights is transparency, not correctness.

### Pipeline pseudocode

```python
def build_prerequisite_graph(chunks):
    concepts   = {c.id: resolve_concept(c) for c in chunks}   # title-derived
    isa        = extract_isa(chunks, concepts)                # S2
    explicit   = extract_explicit_dependencies(chunks, concepts)  # S1
    siblings   = sibling_sets(isa, chunks)                    # V1

    edges = []
    for a, b in candidate_pairs(chunks, concepts):            # stage [2]
        if is_sibling(a, b, siblings) or is_co_definer(a, b):
            continue

        mentions = find_mentions(b, concepts[a.id])
        if mentions and all(m.contrastive for m in mentions):  # V3
            continue
        if mutual_reference(a, b, concepts):                   # V4
            continue

        strong, medium, weak = [], [], []
        if (a.id, b.id) in explicit:            strong.append(Ev("explicit_dependency"))
        if isa.get(b.id) == concepts[a.id]:     strong.append(Ev("is_a"))
        if term_in_defining_sentence(b, concepts[a.id]):  medium.append(Ev("definitional"))
        if is_aggregation_chunk(b) and mentions:          medium.append(Ev("aggregation"))
        if instantiates(b, concepts[a.id]):               medium.append(Ev("instantiation"))
        if same_section_progression(a, b):                medium.append(Ev("section_progression"))
        if mentions:                                      weak.append(Ev("mention"))
        if distinctive_overlap(a, b) >= 3:                weak.append(Ev("cooccurrence"))

        if accept(Evidence(strong, medium), suppressors=[]):
            edges.append(Edge(a, b, tier=tier_of(strong, medium),
                              evidence=dict(strong=strong, medium=medium, weak=weak)))

    return break_cycles(edges)            # stage [6]
```

### Candidate generation (stage 2)

A pair is a candidate if **any** of: B mentions A's concept; A and B share ≥3
distinctive terms; A and B are adjacent in one section; an is-a or explicit
statement names both. This is deliberately generous — it only decides what gets
*examined*.

### Document order (stage 6)

Document order is **no longer a hard directional filter**. Strong evidence may
run backwards, because real materials contain forward references — your own
Introduction says the states require shape and volume, while Shape and Volume
are presented *after* the states. Under the current hard rule that dependency is
undetectable.

Instead: build edges, then run cycle detection. In any cycle, drop the
lowest-tier edge (medium before strong); on a tie, drop the one running against
document order. Record every dropped edge for review. Document order thus
becomes a **tie-breaker of last resort** rather than a precondition — which also
removes the circularity you flagged, since order no longer justifies an edge, it
only arbitrates between two edges already justified by evidence.

### Specific handling

| Case | Rule |
|---|---|
| **Siblings** | V1 — shared is-a parent or parallel section position. No edge, ever. |
| **Examples** | M3 — `concept → example chunk`, never the reverse. |
| **Comparisons** | M2 — every non-contrastively-treated concept → the comparison chunk. |
| **Properties** | Not a prerequisite by default. Promoted only when S1 names it (your Introduction case). |
| **Definitions** | Only via S2 or M1. Being *a* definition is not evidence — this is what C4 got wrong. |
| **Multi-concept** | A chunk may take several prerequisites (Comparing States takes three). Fan-in is expected and correct. |

---

## E. The States of Matter graph, reprocessed

**Strong prerequisite edges**

| Edge | Basis |
|---|---|
| `Matter → Solid` | S2 — "A solid is a state of matter" |
| `Matter → Liquid` | S2 |
| `Matter → Gas` | S2 |
| `Solid → Changes of State` | S1 — "understanding each state should come first" |
| `Liquid → Changes of State` | S1 |
| `Gas → Changes of State` | S1 |
| `Shape/Volume/Particles → Comparing the Three States` | S1, corrected — see note below |

**Possible / supporting (accepted only with two medium types)**

| Edge | Basis |
|---|---|
| `Solid → Comparing the Three States` | M2 aggregation + M1 definitional |
| `Liquid → Comparing`, `Gas → Comparing` | same |
| `Solid → Everyday Examples` | M3 instantiation ("An ice cube is a solid") + M2 |
| `Liquid → Flow`, `Gas → Flow` | M2 aggregation — they are the subject of the claim |

**Must NOT be edges**

| Non-edge | Why |
|---|---|
| `Solid → Liquid`, `Liquid → Gas` | V1 siblings — shared is-a parent `matter` |
| `Solid → Flow` | V3 contrastive — "**while** solids normally do not" |
| `Matter → Everyday Examples` | The examples instantiate the *states*, not matter directly. Transitively implied; not asserted. |
| `Matter → Particle arrangement` | Current C4 artifact: both are definitions. No relation of substance. |
| `Comparing → Changes of State` | **Your proposed graph includes this; I think it is wrong.** Changes of State needs the three states, which S1 already gives. It does not need the *comparison* of them. Including it asserts a dependency the text does not support. |
| `Shape → Solid` | **An error in the first draft of this spec, corrected on review.** The sentence reads "To understand **the difference between** these states, we need to look at… the shape and volume". Its object is the *comparison*, not each state. Shape and volume are the dimensions along which the states are described, not topics a learner must complete first — property-of is not prerequisite-of. Independently, the sentence sits in material 15 while the `Shape` and `Volume` chunks belong to material 14, and derivation is per-material, so the edge could not have formed in any case. |

Estimated output: roughly **12–18 edges for the 10-node lesson**, against 10
today — but they are different edges, and every one is traceable to a sentence.

---

## E.2 The sequence builder

Two outputs, from two sources, kept apart:

| | supplies |
|---|---|
| **Prerequisite DAG** | what *must* precede what |
| **Author's presentation order** | the default choice where the DAG is silent |

```python
def build_generic_sequence(chunks, edges):
    """Topological order, with the author breaking every tie."""
    remaining = {c.id: incoming_count(c, edges) for c in chunks}
    order = []
    while remaining:
        eligible = [cid for cid, n in remaining.items() if n == 0]
        if not eligible:
            raise GraphCycleError(sorted(remaining))
        # The graph does not rank eligible chunks; the author does.
        nxt = min(eligible, key=lambda cid: (
            not continues_previous(cid, order),   # keep split parts adjacent
            section_index(cid),                   # keep sections coherent
            source_order(cid),                    # author's sequence
        ))
        order.append(nxt)
        release_dependents(nxt, edges, remaining)
    return order
```

Rules, in priority order:

1. Never violate a prerequisite edge.
2. Keep continuation chunks ("Part 1 of 3") adjacent.
3. Keep a section's chunks together where the graph permits.
4. Among the remaining eligible chunks, follow the author's order.

Rule 4 is the layer reinforcement learning later replaces. It never overrides
rule 1.

On the sample: after `Matter`, all three states become eligible simultaneously.
The graph is silent on their order; the author says Solid, Liquid, Gas. Output:

```
Matter → Solid → Liquid → Gas → Comparing the Three States → Changes of State
```

The author's sequence is preserved **without any claim that Solid is a
prerequisite of Liquid**. That is the distinction the whole redesign turns on.

Note this supersedes the current implementation, which teaches in pure document
order. That was safe only while every edge was forced to run forward; now that
S1 may run backwards, document order is no longer guaranteed to be a valid
topological order, and the sequence must be built rather than assumed.

## F. Why the source document's order matters

Because the document is not raw text — it is an artefact of expert design, and
we would be discarding evidence for no reason.

Three separate things must not be conflated:

1. **Source presentation order** — the sequence the author chose. Evidence about
   instructional intent.
2. **Semantic relationship** — is-a, example-of, comparison, property-of. What
   two passages have to do with each other.
3. **Pedagogical prerequisite** — understanding A is reasonably necessary for
   understanding B. The only one that becomes an edge.

The literature is consistent with this: textbook order is mined *to estimate*
prerequisites and used as a constraint (an earlier concept may be a prerequisite
of a later one; the reverse cannot follow from order alone). Nobody treats
author order as identical to the prerequisite relation.

So order earns its place as: (i) a tie-breaker when two justified edges conflict,
and (ii) a component of M4, where an explanation following an introduction
*within a section* signals instructional progression. It never justifies an edge
by itself. That is what keeps the reasoning non-circular — order arbitrates
between conclusions, it does not generate them.

And the honest counterpart: **the generic path may end up close to source
order.** That is a success, not a failure. If a DepEd author sequenced the lesson
coherently, a correct prerequisite graph should largely agree with them. The
output of this work is not a different order — it is an explicit, machine-readable
dependency structure that did not previously exist.

---

## G. How this fits with RL later

```
Stage 1  (this design)
  source material → semantic evidence → prerequisite graph → generic path

Stage 2  (later, not now)
  BKT learner state + generic graph → RL/DQN → personalised sequence
```

The graph is the action-space constraint and the remediation map. RL chooses
*among* pedagogically valid moves; it does not decide what is a prerequisite.
Keeping the two stages separate is what makes the RL stage evaluable at all — a
learned policy over an unvalidated graph could not be attributed.

---

## Honest critique of this design, and of the brief

**1. Your strong-evidence tier is very thin in this corpus.** Measured: explicit
pedagogical language appears in **2 of 67** chunks; is-a patterns in **6 of 67**.
Worse, the 9-chunk `Lesson-1` PDF yields **zero** is-a matches — it says "A solid
has a definite shape", never "is a state of matter". A strong-evidence-only rule
gives that PDF an empty graph. The two-medium rule exists precisely to carry
those cases, and it is the part of this design most likely to be wrong.

**2. Your proposed graph contains an edge I would reject.** `Comparing States →
Changes of State`, as argued in section E.

**3. Full relationship typing is not feasible unsupervised here.** The brief
lists is-a, part-of, property-of, example-of, comparison, contrast, causal,
sequence, definition, elaboration. Reliably distinguishing causal from
elaboration in Grade 1 prose without training data is a research problem in its
own right. This design commits to **four** detectable types (is-a, instantiation,
aggregation, explicit dependency) plus suppressors, because those are the ones
with unambiguous surface cues in your actual text. Fewer types, each reliable,
beats ten that each fire wrongly.

**4. Fixing extraction may beat improving inference.** 84% of chunks have no
section title. M4 and half of V1 depend on section structure. Capturing headings
during PDF extraction would likely improve this graph more than any scoring
change — and it is easier work.

**5. This design will produce a much sparser graph, and you should confirm you
want that.** Expect on the order of 15–40 edges where you now have 416. If
remediation needs a fallback for a concept with no prerequisites, that is a
product decision to make deliberately rather than discover.

---

## Validation

No labelled data exists, and none is proposed. Instead:

1. Derive graphs with both the old and new methods on the same two PDFs.
2. Sample **40 edges** from each (all of the new one if it yields fewer).
3. Present each as a plain sentence — *"Is understanding **Matter** reasonably
   necessary before understanding **Solid**? yes / no / unsure"* — with the
   evidence sentence shown, to one Science teacher, blind to which method
   produced it.
4. Report precision per method, and per evidence tier for the new one.
5. Inspect every false positive by hand; they identify which evidence type is
   over-firing.

This measures precision only, not recall — you cannot measure recall without an
exhaustive gold graph. State that limitation rather than implying otherwise.
Forty judgements is roughly an hour of a teacher's time, which is the realistic
ceiling for a student project, and it is enough to tell a 40%-precision method
from an 85% one.

---

## Revision history

**Rev 2 — after external review.** Six changes, five adopted and one refined:

1. **S2 (is-a) is now conditional**, not unconditional. Adopted: taxonomic
   dependency is not automatically instructional dependency. Refined rather than
   simply demoted — see the note below.
2. **`Shape → Solid` removed.** Adopted; it was an error, corrected in section E.
3. **Suppressors are overridable by S1**, except V2. Adopted: siblings can be
   deliberately sequenced, and mutual reference makes direction ambiguous rather
   than disproving dependency.
4. **The two-medium rule is labelled an unvalidated operating decision.**
   Adopted, with thesis wording supplied.
5. **Graph and sequence separated**, with a sequence builder that takes the
   author's order as the tie-breaker among topologically eligible chunks.
   Adopted; this is the strongest of the six and it supersedes the current
   implementation.
6. **Extraction metadata.** Already flagged; reinforced.

### Where this design departs from the review

The review proposed demoting is-a to "medium-high, requiring instructional
context". Applied literally, that breaks the graph, and the reason is worth
recording.

Measured in this corpus: explicit pedagogical language appears in **2 of 67**
chunks and is-a in **6 of 67**. If is-a can no longer create an edge alone, the
STRONG tier fires on approximately three chunks in total, and essentially the
whole graph comes to rest on the two-medium rule — the very rule the review
identifies as arbitrary. Worse, for `Matter → Solid` the is-a sentence *is* the
defining sentence, so S2 and M1 are one observation counted twice; under a
"needs corroboration" rule that edge would fail on a technicality, and it is the
one edge every reader agrees on.

The condition adopted here — **is-a is strong when the parent concept is itself
taught in the same material** — answers the objection at its source. The
whale/mammal problem is an *ontological* is-a, imported from outside the lesson.
An is-a stated by the author, about a concept the author also teaches, is
instructional framing. And where the parent is not taught, no edge can form
anyway, because edges exist only between chunks.
