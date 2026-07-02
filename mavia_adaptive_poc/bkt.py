"""Bayesian Knowledge Tracing update (Box 5)."""

P_GUESS = 0.20
P_SLIP = 0.10
P_LEARN = 0.15


def update_mastery(prior, correct, p_guess=P_GUESS, p_slip=P_SLIP, p_learn=P_LEARN):
    """Standard BKT posterior update followed by the learning-transfer step."""
    if correct:
        num = prior * (1 - p_slip)
        denom = num + (1 - prior) * p_guess
    else:
        num = prior * p_slip
        denom = num + (1 - prior) * (1 - p_slip)

    posterior = num / denom if denom > 0 else prior
    updated = posterior + (1 - posterior) * p_learn
    return min(max(updated, 0.0), 1.0)
