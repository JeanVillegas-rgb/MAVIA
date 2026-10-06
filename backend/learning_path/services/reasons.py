"""One plain sentence explaining a prerequisite link, for the review screen.

Built from the evidence ``criteria.decide_pairs`` (rule ``reference-order``) and
``course_criteria.decide_course_pairs`` (rule ``course``) store on each derived
row. A row with no evidence was made by the teacher.
"""


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
    orientation-free ones (weak terms, disagreeing files) are read from that list.
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


def link_reason(evidence, prerequisite_title, dependent_title):
    a, b = prerequisite_title, dependent_title
    evidence = evidence or {}
    rule = evidence.get("rule")
    if rule == "course":
        return _course_reason(evidence, a, b)
    if rule == "reference-order":
        return _reference_order_reason(evidence, a, b)
    return "Added by you."
