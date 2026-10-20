"""One plain sentence explaining a prerequisite link, for the review screen."""


_CONTRADICTION_SENTENCES = {
    "pdfs_disagree": "The files put them in different orders.",
}


def _naming(names, a, b):
    """"B names A", or which of A's parts B names."""
    parts = names.get("parts") or []
    if not parts:
        return f"{b} names {a}."
    listed = " and ".join(parts) if len(parts) <= 2 else f"{', '.join(parts[:2])} and {len(parts) - 2} more"
    return f"{b} names {listed}, {'part' if len(parts) == 1 else 'parts'} of {a}."


def _reference_order_reason(evidence, a, b):
    """The reason sentence for a reference-order link."""
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
    if names.get("use_back", 0) > names.get("use", 0) and votes.get("heading") != 1:
        parts.append(f"But {a}'s text names {b} more than the reverse.")
    refers = names.get("use", 0) > 0 or terms.get("use", 0) > 0
    refers_back = names.get("use_back", 0) > 0 or terms.get("use_back", 0) > 0
    if refers_back and not refers and votes.get("heading") != 1:
        parts.append(f"But only {a}'s text refers to {b}.")
    if "weak_terms" in contradictions:
        words = (terms.get("owned") or []) + (terms.get("owned_back") or [])
        listed = f" ({', '.join(words[:3])})" if words else ""
        wording = "single shared words link them" if len(words) > 1 else "one shared word links them"
        parts.append(f"Only {wording}{listed}; please confirm.")
    parts.extend(_CONTRADICTION_SENTENCES[key] for key in _CONTRADICTION_SENTENCES if key in contradictions)
    return " ".join(parts)


def link_reason(evidence, prerequisite_title, dependent_title):
    a, b = prerequisite_title, dependent_title
    evidence = evidence or {}
    rule = evidence.get("rule")
    if rule == "reference-order":
        return _reference_order_reason(evidence, a, b)
    return "Added by you."
