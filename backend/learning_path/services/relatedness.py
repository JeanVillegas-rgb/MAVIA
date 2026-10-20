"""Relatedness between two concepts; symmetric, so it never gives a direction."""


def _has_rows(vectors):
    return vectors is not None and len(vectors) > 0


def best_match_average(first_vectors, second_vectors):
    """Mean, over the first concept's sentences, of each one's closest sentence in the second."""
    if not (_has_rows(first_vectors) and _has_rows(second_vectors)):
        return 0.0
    return float((first_vectors @ second_vectors.T).max(axis=1).mean())


def relatedness(first, second):
    return (
        best_match_average(first.vectors, second.vectors)
        + best_match_average(second.vectors, first.vectors)
    ) / 2
