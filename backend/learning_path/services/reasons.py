"""One plain sentence explaining a prerequisite link, for the review screen.

Built from the evidence ``criteria.decide_pairs`` stores on each derived row (v6 or v7);
rows from older versions (v4, v5) keep their wording until re-derived. A row
with no evidence was made by the teacher.
"""


_CLUE_NAMES = {
    "name": "the name", "terms": "the terms", "meaning": "the meaning",
    "heading": "the headings", "order": "the files' order",
}


def _fused_reason(evidence, a, b):
    votes = evidence.get("votes") or {}
    records = evidence.get("records") or {}
    parts = []
    if votes.get("terms") == 1:
        owned = (records.get("terms") or {}).get("owned") or []
        listed = f" ({', '.join(owned[:3])})" if owned else ""
        parts.append(f"{b} uses terms {a} explains{listed}.")
    if votes.get("meaning") == 1:
        parts.append(f"{b}'s sentences refer to {a}'s ideas.")
    if votes.get("name") == 1:
        parts.append(f"{b} names {a}.")
    if votes.get("heading") == 1:
        parts.append(f"{b} sits under a heading naming {a}.")
    if votes.get("order") == 1:
        order = records.get("order") or {}
        parts.append(f"{order.get('agree')} of {order.get('pdfs')} files teach {a} first.")
    against = [_CLUE_NAMES[clue] for clue in _CLUE_NAMES if votes.get(clue) == -1]
    if against:
        parts.append(f"Against it: {', '.join(against)}.")
    if not any(votes.get(clue) for clue in ("name", "terms", "meaning")):
        parts.append("The text says nothing either way.")
    if evidence.get("disagreement"):
        parts.append("The text reads the other way; this follows how the files are organised.")
    if evidence.get("parallel"):
        parts.append(f"The files present {a} and {b} side by side under one heading.")
    if evidence.get("semantic") is False:
        parts.append("The meaning check was unavailable.")
    if evidence.get("confidence") is not None:
        parts.append(f"Confidence {evidence['confidence']:.2f}.")
    return " ".join(parts)


def _course_reason(evidence, a, b):
    votes = evidence.get("votes") or {}
    records = evidence.get("records") or {}
    parts = []
    if votes.get("terms") == 1:
        owned = (records.get("terms") or {}).get("owned") or []
        listed = f" ({', '.join(owned[:3])})" if owned else ""
        parts.append(f"{b} uses terms {a} explains{listed}.")
    if votes.get("meaning") == 1:
        parts.append(f"{b}'s sentences refer to {a}'s ideas.")
    if votes.get("name") == 1:
        parts.append(f"{b} names {a}.")
    if evidence.get("contradicts_outline"):
        parts.append(f"This contradicts your outline: {a}'s topic comes later.")
    else:
        parts.append("This follows your outline.")
    if evidence.get("semantic") is False:
        parts.append("The meaning check was unavailable.")
    if evidence.get("confidence") is not None:
        parts.append(f"Confidence {evidence['confidence']:.2f}.")
    return " ".join(parts)


_CONTRADICTION_SENTENCES = {
    "figure": "One of them is a figure, so its place in the file does not give the order.",
    "no_shared_pdf": "They come from different files, so their order is a guess.",
    "pdfs_disagree": "The files put them in different orders.",
}


def _naming(names, a, b):
    """"B names A", or which of A's parts B names when that is how it refers to A (6.2)."""
    parts = names.get("parts") or []
    if not parts:
        return f"{b} names {a}."
    listed = " and ".join(parts) if len(parts) <= 2 else f"{', '.join(parts[:2])} and {len(parts) - 2} more"
    return f"{b} names {listed}, {'part' if len(parts) == 1 else 'parts'} of {a}."


def _reference_order_reason(evidence, a, b):
    """``records`` are oriented prerequisite-first, so every sentence reads from them.

    The stored ``contradictions`` name the PDF's earlier and later concept, which
    is the dependent when a figure or the names reversed the order; only the
    orientation-free ones (figure, files) are read from that list.
    """
    votes = evidence.get("votes") or {}
    records = evidence.get("records") or {}
    names = records.get("name") or {}
    terms = records.get("terms") or {}
    contradictions = evidence.get("contradictions") or []
    parts = []
    if votes.get("heading") == 1:
        parts.append(f"{b} sits under a heading naming {a}.")
    if names.get("use", 0) > 0:
        parts.append(_naming(names, a, b))
    if terms.get("use", 0) > 0:
        owned = terms.get("owned") or []
        listed = f" ({', '.join(owned[:3])})" if owned else ""
        parts.append(f"{b} uses terms {a} explains{listed}.")
    source = evidence.get("direction_from")
    if source == "pdf_order":
        parts.append(f"{a} comes first in the lesson.")
    elif source == "merged_order":
        parts.append(f"{a} comes first in the topic's combined order.")
    elif source == "figure":
        parts.append(f"The figure's description refers to {a}.")
    elif source == "name":
        parts.append(f"The names put {a} first; the files do not settle the order.")
    elif source == "pdf_agreement":
        order = records.get("order") or {}
        parts.append(f"{order.get('agree')} of {order.get('pdfs')} files teach {a} first; the text says nothing either way.")
    if names.get("use_back", 0) > names.get("use", 0):
        parts.append(f"But {a}'s text names {b} more than the reverse.")
    refers = names.get("use", 0) > 0 or terms.get("use", 0) > 0
    refers_back = names.get("use_back", 0) > 0 or terms.get("use_back", 0) > 0
    if refers_back and not refers and votes.get("heading") != 1:
        parts.append(f"But only {a}'s text refers to {b}.")
    if "weak_terms" in contradictions:
        words = (terms.get("owned") or []) + (terms.get("owned_back") or [])
        listed = f" ({', '.join(words[:3])})" if words else ""
        # No passage holds two of them, but different passages can each hold a different one.
        wording = "single shared words link them" if len(words) > 1 else "one shared word links them"
        parts.append(f"Only {wording}{listed}; please confirm.")
    parts.extend(_CONTRADICTION_SENTENCES[key] for key in _CONTRADICTION_SENTENCES if key in contradictions)
    return " ".join(parts)


_VOTE_NAMES = {"hierarchy": "the hierarchy", "order": "the lesson order", "reference": "the references"}

_SOURCE_SENTENCES = {
    "outvoted_order": "This outvoted the lesson order, which teaches {b} first.",
    "order_only": "Direction from the lesson order only; the text gives nothing else to go on.",
    "contested": "The evidence disagrees; choose the direction.",
    "merged_order": "Nothing settles the direction; it follows the topic's combined order.",
    "hierarchy": "Only the hierarchy points this way; please confirm.",
    "reference": "Only the references point this way; please confirm.",
}


def _three_vote_reason(evidence, a, b):
    """v7: one sentence per vote for the link, what stands against it, how it was settled."""
    votes = evidence.get("votes") or {}
    records = evidence.get("records") or {}
    parts = []
    if votes.get("hierarchy") == 1:
        if (records.get("hierarchy") or {}).get("from") == "heading":
            parts.append(f"{b} sits under a heading naming {a}.")
        else:
            parts.append(f"{a} is the broader idea: almost everywhere {b} appears, {a} does too.")
    if votes.get("reference") == 1:
        parts.append(f"{b} refers to {a} more than the reverse.")
    if votes.get("order") == 1:
        parts.append(f"The lesson teaches {a} first.")
    source = evidence.get("direction_from")
    against = [_VOTE_NAMES[vote] for vote in _VOTE_NAMES if votes.get(vote) == -1]
    if against and source != "outvoted_order":
        parts.append(f"Against it: {', '.join(against)}.")
    if source in _SOURCE_SENTENCES:
        parts.append(_SOURCE_SENTENCES[source].format(a=a, b=b))
    return " ".join(parts)


def _shortlist_reason(evidence, a, b):
    if evidence.get("confirmed"):
        return _course_reason(evidence, a, b)
    return (f"One of the 3 closest matches for {b} in its earlier topic (rank {evidence.get('rank')}). "
            "Please confirm or dismiss.")


def _closest_reason(evidence, a, b):
    if evidence.get("confirmed"):
        return f"{b} is closest in meaning to {a}, names it, and shares {', '.join(evidence.get('shared_words') or [])}."
    return (f"{b} is closest in meaning to {a}, clearly closer than the concepts of its own topic. "
            "Please confirm or dismiss.")


def link_reason(evidence, prerequisite_title, dependent_title):
    a, b = prerequisite_title, dependent_title
    evidence = evidence or {}
    rule = evidence.get("rule")

    if rule == "course":
        return _course_reason(evidence, a, b)
    if rule == "course-shortlist":
        return _shortlist_reason(evidence, a, b)
    if rule == "course-closest":
        return _closest_reason(evidence, a, b)
    if rule == "three-votes":
        return _three_vote_reason(evidence, a, b)
    if rule == "reference-order":
        return _reference_order_reason(evidence, a, b)
    if rule == "fusion":
        return _fused_reason(evidence, a, b)
    if rule == "definition":
        sentence = (evidence.get("definition") or {}).get("sentence", "")
        return f"{b}'s definition uses {a}: “{sentence}”"
    if rule == "containment":
        return f"{b} sits under the heading “{a}”."
    if rule == "reference":
        ref = evidence.get("reference") or {}
        n, p = ref.get("passages_forward"), ref.get("passages_backward")
        if not n:
            return f"{b}'s text names {a} more often than {a}'s text names {b}."
        k = round(ref.get("prw_forward", 0) * n)
        m = round(ref.get("prw_backward", 0) * (p or 0))
        back = f"{a}'s text never names {b}." if m == 0 else f"{a}'s text names {b} in {m} of {p}."
        return f"{b}'s text names {a} in {k} of {n} passages; {back}"
    if rule == "conflict":
        return "The lesson files point both ways; choose one."
    return "Added by you."
