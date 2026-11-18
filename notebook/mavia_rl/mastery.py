"""The mastery ESTIMATOR - what the agent actually observes.

This is deliberately separate from the learner simulator (`learner.py`), which
holds the hidden truth. The agent only ever sees this estimate, exactly like
production: BKT tracking a latent it can't measure directly.

Swap `BKTMasteryModel` for a fitted version once you have real
`StudentResponse` data - keep the same interface.
"""

from __future__ import annotations

import numpy as np

from .config import Config


class BKTMasteryModel:
    """Per-node Bayesian Knowledge Tracing plus bookkeeping the state vector
    needs (attempts, recency)."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.n = cfg.n_nodes
        self.mastery = np.zeros(self.n, dtype=np.float32)
        self.attempts = np.zeros(self.n, dtype=np.int64)
        self.last_seen = np.full(self.n, -1, dtype=np.int64)
        self._step = 0

    def reset(self, rng: np.random.Generator, entry_nodes: list[int]) -> None:
        c = self.cfg
        self.mastery = rng.uniform(c.starting_mastery_low, c.starting_mastery_high,
                                   size=self.n).astype(np.float32)
        # entry nodes start a little warmer - a learner usually has *some*
        # exposure to foundational material.
        for e in entry_nodes:
            self.mastery[e] = float(rng.uniform(c.starting_mastery_low + 0.05,
                                                c.starting_mastery_high + 0.10))
        self.attempts[:] = 0
        self.last_seen[:] = -1
        self._step = 0

    # -- updates -------------------------------------------------------
    def observe(self, node_id: int, correct: bool) -> float:
        """Fold one graded answer into the estimate. Returns the new mastery."""
        c = self.cfg
        prior = float(self.mastery[node_id])
        if correct:
            num = prior * (1 - c.p_slip)
            den = num + (1 - prior) * c.p_guess
        else:
            num = prior * c.p_slip
            den = num + (1 - prior) * (1 - c.p_guess)
        posterior = num / max(den, 1e-6)
        new = posterior + (1 - posterior) * c.p_learn
        new = float(np.clip(new, 0.001, 0.999))
        self.mastery[node_id] = new
        self.attempts[node_id] += 1
        self.last_seen[node_id] = self._step
        return new

    def decay_untouched(self, touched_node: int) -> np.ndarray:
        """Multiplicative forgetting on every node except the one just served.
        Returns the per-node mastery lost (>= 0) for reward shaping."""
        before = self.mastery.copy()
        factor = 1.0 - self.cfg.decay_rate
        mask = np.ones(self.n, dtype=bool)
        mask[touched_node] = False
        self.mastery[mask] *= factor
        self._step += 1
        return np.maximum(before - self.mastery, 0.0)

    # -- readouts ---------------------------------------------------------
    def confidence(self) -> np.ndarray:
        """0 with no attempts, approaching 1 as evidence accumulates."""
        return (self.attempts / (self.attempts + 3.0)).astype(np.float32)

    def steps_since_seen(self) -> np.ndarray:
        raw = np.where(self.last_seen < 0, self._step + 1, self._step - self.last_seen)
        return np.clip(raw / max(self.cfg.max_steps, 1), 0.0, 1.0).astype(np.float32)

    def n_mastered(self) -> int:
        return int(np.sum(self.mastery >= self.cfg.mastered_threshold))
