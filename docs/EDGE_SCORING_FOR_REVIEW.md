# MAVIA edge scoring — current implementation and output

Copy everything below the line into ChatGPT (or any reviewer) for critique.

---

## Context

I am building a system that turns DepEd lesson PDFs into an adaptive learning
app for Grade 1 Science. A PDF is chunked into "learning objects" (short
passages, ~15–60 words each, with a short title like "Solid" or "Matter"). I
derive a **prerequisite graph** between those passages.

The graph is used for **remediation** — when a learner fails a question, it says
which earlier concepts to send them back to. It is **not** used to reorder the
lesson: teaching follows the PDF's own order, because every derived edge is
constrained to run forward through the document, so document order is always a
valid topological order of the graph.

I have **no labelled prerequisite data** and no realistic way to obtain it, so
the method must be unsupervised.

## Design intent

Two decisions are kept separate:

- **Document order decides direction.** An edge may only run from an earlier
  passage to a later one. This also guarantees the graph is acyclic.
- **Evidence decides whether an edge exists.** Four criteria vote, each worth
  exactly one vote, because I have no data with which to justify weights.

Basis I used: RefD / reference asymmetry (Liang et al. 2015), temporal order,
co-occurrence, and equal-weight multi-criteria voting without labelled data
(Alatrash et al. 2025, which uses ten criteria and a threshold of 0.28).

## The formula

```
For an ordered pair (A earlier, B later) in the same PDF:

  votes_forward  = number of criteria voting A → B
  votes_backward = number of criteria voting B → A
  score          = (votes_forward - votes_backward) / 4      # range [-1, 1]

  create edge A → B  iff  score >= 0.25                      # one net vote
```

`weight` on the stored edge = `score`.

### The four criteria

**C1 — body reference asymmetry (RefD principle).**
`refs(B→A)` = B's body text mentions a concept term taken from A's title.
Votes A→B only if `refs(B→A)` is true AND `refs(A→B)` is false. Mutual mentions
and mutual silence both produce no vote.

**C2 — section reference asymmetry.** Same test against section headings.

**C3 — distinctive co-occurrence.** A and B share ≥3 terms whose document
frequency is ≤ the distinctive limit (15% of passages, floor of 2). Symmetric,
so it votes only in the direction document order already fixed.

**C4 — definition scope.** The material's opening definition votes for every
later passage that is also a definition. A "definition" is detected by a regex
for an opening verb: `is|are|means|refers to|has|have`.

### Two edges that bypass the vote

- **Chunk continuation** — "(Part 1 of 3)" → "(Part 2 of 3)". These are one
  passage my chunker split, so they are linked structurally at weight 1.0.
- **Teacher-authored** edges, weight 1.0.

A "co-definer" rule suppresses edges between two passages claiming the same
concept term (e.g. two PDFs both titled "Solid"), except chunk continuation.

## The code

```python
VOTE_THRESHOLD = 0.25
CRITERIA = ("body_reference", "section_reference", "cooccurrence", "definition_scope")

for index, dependent in enumerate(document_order):
    for prerequisite in document_order[:index]:          # forward only
        if (prerequisite.id, dependent.id) in continuation_pairs:
            continue
        if prerequisite.id in co_definers[dependent.id]:
            continue

        forward, backward = {}, {}

        # C1 body reference asymmetry
        dependent_refs = _refers_to(content[dependent.id], prerequisite, concepts[dependent.id])
        prerequisite_refs = _refers_to(content[prerequisite.id], dependent, concepts[prerequisite.id])
        if dependent_refs and not prerequisite_refs:
            forward["body_reference"] = dependent_refs
        elif prerequisite_refs and not dependent_refs:
            backward["body_reference"] = prerequisite_refs

        # C2 section reference asymmetry (same test on section headings)
        ...same shape as C1...

        # C3 distinctive co-occurrence
        shared = {t for t in terms[prerequisite.id] & terms[dependent.id]
                  if frequencies[t] <= distinctive_limit}
        if len(shared) >= 3:
            forward["cooccurrence"] = sorted(shared)[:10]

        # C4 definition scope
        if (opening_definition_id is not None
                and prerequisite.id == opening_definition_id
                and dependent.id in definition_ids):
            forward["definition_scope"] = {...}

        score = (len(forward) - len(backward)) / len(CRITERIA)
        if score < VOTE_THRESHOLD:
            continue
        create_edge(prerequisite, dependent, weight=score, evidence={...})
```

`_refers_to` is whole-word containment of a singularised concept term. Concept
terms come from a passage's title only, and a title longer than a few words is
treated as prose that defines nothing.

## Resulting graph — PDF 1 (10 passages)

Every node, in document order, with its derived depth:

```
  1. This figure illustrates how the arrangement of tiny particles...  depth=0  prereqs=0
  2. Matter                                                            depth=0  prereqs=0
  3. Solid                                                             depth=1  prereqs=1
  4. Liquid                                                            depth=1  prereqs=1
  5. Gas                                                               depth=1  prereqs=1
  6. Shape                                                             depth=0  prereqs=0
  7. Volume                                                            depth=0  prereqs=0
  8. Particle arrangement                                              depth=1  prereqs=1
  9. Flow                                                              depth=2  prereqs=2
 10. Everyday Examples                                                 depth=2  prereqs=4
```

All 10 edges:

```
  Matter -> Solid                 score=0.25  criteria=[definition_scope]
  Matter -> Liquid                score=0.25  criteria=[definition_scope]
  Matter -> Gas                   score=0.25  criteria=[definition_scope]
  Matter -> Particle arrangement  score=0.25  criteria=[definition_scope]
  Matter -> Everyday Examples     score=0.25  criteria=[definition_scope]
  Solid  -> Flow                  score=0.25  criteria=[body_reference]
  Solid  -> Everyday Examples     score=0.25  criteria=[body_reference]
  Liquid -> Everyday Examples     score=0.25  criteria=[body_reference]
  Gas    -> Flow                  score=0.25  criteria=[body_reference]
  Gas    -> Everyday Examples     score=0.25  criteria=[body_reference]
```

## Resulting graph — PDF 2 (55 passages)

```
55 nodes, 416 edges, 9 unconnected roots, max depth 14

score distribution:
  0.25 (one criterion)   385 edges   (93%)
  0.50 (two criteria)     14 edges
  0.75 (three criteria)    1 edge
  1.00 (structural)       16 edges   (chunk continuation)

criteria combinations:
  body_reference                                 365
  chunk_continuation                              16
  cooccurrence                                    15
  body_reference + cooccurrence                    9
  definition_scope                                 5
  body_reference + definition_scope                4
  body_reference + cooccurrence + definition_scope 1
  cooccurrence + definition_scope                  1
```

## Things I already know are weak

- **The vote almost never combines.** 93% of edges rest on a single criterion,
  and 88% are `body_reference` alone. Nominally a four-criteria vote; in
  practice it is one criterion with three that rarely fire.
- **The threshold of 0.25 is one net vote**, so voting is currently equivalent
  to "any one criterion fired and none fired the other way".
- **No calibration.** Every cutoff (0.25 here, 3 shared terms, 15% document
  frequency) is a chosen operating value, not measured.
- **416 edges over 55 nodes** is very dense — about 7.5 per node.
- **The definition regex is loose**: "Zebras *have* stripes" is detected as a
  definition because `have` is in the verb list.
- **C2 (section reference) never fired once** on either PDF.
- **RefD adaptation.** The published metric measures Wikipedia hyperlinks and
  computes a ratio; I test passage-level text mentions. I describe it as the
  RefD *principle*, not an implementation of RefD.

## What I want reviewed

1. Is equal-weight voting a defensible combination method here, given 93% of
   edges are decided by one criterion? Should the threshold be higher, or the
   criteria changed?
2. Is the density (7.5 edges per node) a problem for a remediation graph, and
   what would a sensible target be?
3. Is my adaptation of RefD to passage-level mentions sound, or is calling it
   the "RefD principle" still overclaiming?
4. Is C4 (definition scope) a legitimate proxy for taxonomic/category
   containment, or should it be dropped as unprincipled?
5. Given no labelled data, is there a cheaper validation than full expert
   annotation — e.g. a small sample a teacher could check that would still say
   something meaningful about precision?
6. Is "teach in document order, use the graph only for dependencies" the right
   call, or am I giving up the main value of building a graph?
