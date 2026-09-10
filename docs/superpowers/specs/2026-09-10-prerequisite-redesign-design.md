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
generic learning path
```

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

**S2 · Taxonomic is-a.**
*Detects:* `B is a <kind|type|form|state|category> of A`, where the sentence
subject is B's own concept.
*Why it counts:* a subtype cannot be understood without its parent category.
This is the category-containment relation the literature detects from knowledge
bases; here it is stated outright in the text.
*Example (real):* "A solid **is a state of matter** that has a definite shape."
→ `Matter → Solid`.
*Independent:* yes.
*Caveat:* the subject check is essential. Without it, "there **is a** lot of
space between particles" extracts `Gas is-a space`. That false positive occurred
in the simulation.

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

### SUPPRESSORS — veto an edge regardless of other evidence

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
| `Shape → Solid/Liquid/Gas` | S1 — "To understand the difference between these states, we need to look at… the shape and volume" |
| `Volume → Solid/Liquid/Gas` | S1 |
| `Particle arrangement → Solid/Liquid/Gas` | S1 — same sentence |

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

Estimated output: roughly **12–18 edges for the 10-node lesson**, against 10
today — but they are different edges, and every one is traceable to a sentence.

---

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
