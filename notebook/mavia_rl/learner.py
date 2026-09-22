"""The simulated student - the hidden ground truth the agent never sees.

Each episode samples a hidden LEARNER TYPE. A fixed threshold rule can't adapt
to type (it has no way to tell them apart); a policy can, by reading the
pattern of right/wrong answers. That gap is a big part of why RL can beat the
heuristic here - but only if the types actually differ, so keep them distinct.

`LearnerSim.from_calibration(...)` is where a response model fitted to real
`StudentResponse` logs plugs in. Until then the parameters are hand-set but
randomised per episode so the policy can't overfit one exact world.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .graph import LearningPathGraph


@dataclass
class LearnerParams:
    p_learn: float          # how fast practice raises true knowledge
    p_guess: float          # P(correct | doesn't know)
    p_slip: float           # P(wrong | knows)
    prereq_sensitivity: float  # how much weak prereqs drag down performance
    forget_rate: float      # true forgetting per step on untouched nodes


LEARNER_TYPES: dict[str, LearnerParams] = {
    "typical":         LearnerParams(0.15, 0.20, 0.10, 0.5, 0.010),
    "fast":            LearnerParams(0.28, 0.20, 0.08, 0.4, 0.006),
    "slow":            LearnerParams(0.08, 0.18, 0.12, 0.6, 0.014),
    "guesser":         LearnerParams(0.14, 0.38, 0.10, 0.5, 0.012),
    "careless":        LearnerParams(0.16, 0.20, 0.28, 0.5, 0.012),
    "prereq_sensitive": LearnerParams(0.15, 0.18, 0.10, 1.3, 0.011),
}


class LearnerSim:
    def __init__(self, params_by_type: dict[str, LearnerParams] | None = None,
                 jitter: float = 0.15):
        self.params_by_type = params_by_type or LEARNER_TYPES
        self.jitter = jitter
        self._types = list(self.params_by_type)
        self.known: np.ndarray | None = None
        self.type_name: str = ""
        self.params: LearnerParams | None = None

    @classmethod
    def from_calibration(cls, fitted_types: dict[str, LearnerParams]) -> "LearnerSim":
        """Build from parameters fitted to real interaction logs (e.g. one
        LearnerParams per cluster of students)."""
        return cls(params_by_type=fitted_types)

    def reset(self, rng: np.random.Generator, graph: LearningPathGraph) -> None:
        self.type_name = str(rng.choice(self._types))
        base = self.params_by_type[self.type_name]
        # small per-student jitter around the type's archetype
        j = lambda v, lo, hi: float(np.clip(v * (1 + rng.uniform(-self.jitter, self.jitter)), lo, hi))
        self.params = LearnerParams(
            p_learn=j(base.p_learn, 0.03, 0.45),
            p_guess=j(base.p_guess, 0.05, 0.45),
            p_slip=j(base.p_slip, 0.02, 0.40),
            prereq_sensitivity=j(base.prereq_sensitivity, 0.0, 2.0),
            forget_rate=j(base.forget_rate, 0.0, 0.05),
        )
        n = graph.n_nodes
        self.known = rng.uniform(0.02, 0.12, size=n).astype(np.float32)
        for e in graph.entry_nodes():
            self.known[e] = float(rng.uniform(0.10, 0.25))
        self._graph = graph

    # -- interaction --------------------------------------------------
    def _effective_known(self, node_id: int) -> float:
        """True knowledge, discounted when this node's prerequisites are shaky
        (you can 'know' a derived skill but still fail when the foundation
        wobbles). Scaled by the learner's prereq sensitivity."""
        ps = self._graph.prereqs(node_id)
        if not ps:
            return float(self.known[node_id])
        weakest = min(float(self.known[p]) for p in ps)
        gap = max(0.0, 0.9 - weakest)
        penalty = self.params.prereq_sensitivity * 0.4 * gap
        return float(np.clip(self.known[node_id] - penalty, 0.0, 1.0))

    def answer(self, node_id: int, rng: np.random.Generator) -> bool:
        k = self._effective_known(node_id)
        p_correct = k * (1 - self.params.p_slip) + (1 - k) * self.params.p_guess
        correct = bool(rng.random() < p_correct)
        # practice effect - attempting the node teaches something either way,
        # more so when the answer was right.
        gain = self.params.p_learn * (1.0 if correct else 0.5)
        self.known[node_id] += gain * (1 - self.known[node_id])
        return correct

    def decay_untouched(self, touched_node: int) -> None:
        factor = 1.0 - self.params.forget_rate
        mask = np.ones_like(self.known, dtype=bool)
        mask[touched_node] = False
        self.known[mask] *= factor

    def true_fraction_mastered(self, threshold: float) -> float:
        return float(np.mean(self.known >= threshold))
