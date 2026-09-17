"""The RL environment: compose a graph, a mastery estimator, and a learner
simulator into a `reset()/step()` loop.

Action space  : one integer per node - "serve the next question from this node".
State         : per-node features built from the mastery ESTIMATE + static
                graph features, plus a few learner-level aggregates. Fixed
                size = n_nodes * PER_NODE + GLOBAL.
Reward        : learning efficiency - mastery gained per step, minus a step
                cost, minus mastery lost to forgetting elsewhere, plus bonuses
                for crossing the mastered line and for the end-of-session
                fraction mastered.
"""

from __future__ import annotations

from collections import deque

import numpy as np

from .config import Config
from .graph import LearningPathGraph
from .learner import LearnerSim
from .mastery import BKTMasteryModel

PER_NODE = 8   # see _node_block
GLOBAL = 5     # see _global_block


def state_dim(cfg: Config) -> int:
    return cfg.n_nodes * PER_NODE + GLOBAL


def n_actions(cfg: Config) -> int:
    return cfg.n_nodes


class MaviaEnv:
    def __init__(self, graph: LearningPathGraph, cfg: Config,
                 learner: LearnerSim | None = None):
        self.graph = graph
        self.cfg = cfg
        self.learner = learner or LearnerSim()
        self.bkt = BKTMasteryModel(cfg)
        self._rng = np.random.default_rng(cfg.seed)
        self._recent = deque(maxlen=8)
        self._steps = 0

    # -----------------------------------------------------------------
    def reset(self, seed: int | None = None) -> np.ndarray:
        if seed is not None:
            self._rng = np.random.default_rng(seed)
        self.bkt.reset(self._rng, self.graph.entry_nodes())
        self.learner.reset(self._rng, self.graph)
        self._recent.clear()
        self._steps = 0
        return self._state()

    # -- masking --------------------------------------------------------
    def valid_action_mask(self) -> np.ndarray:
        return self.graph.valid_next(self.bkt.mastery, self.cfg.prereq_mastery_required)

    # -- step -------------------------------------------------------
    def step(self, action: int):
        cfg = self.cfg
        node = int(action)

        if not self.valid_action_mask()[node]:
            # the mask should prevent this; guard anyway.
            return self._state(), -1.0, False, {"event": "invalid_action"}

        # Reward is graded against TRUE knowledge (`learner.known`), which the
        # env has but the agent never sees - a policy must not be able to
        # "win" by inflating the BKT estimate through lucky guesses. The
        # agent's observation stays limited to the estimate (see `_state`).
        true_before = self.learner.known.copy()
        was_mastered = true_before[node] >= cfg.mastered_threshold

        correct = self.learner.answer(node, self._rng)     # updates true knowledge
        self.bkt.observe(node, correct)                    # updates the estimate (state only)
        self.learner.decay_untouched(node)                 # true forgetting elsewhere
        self.bkt.decay_untouched(node)                     # estimate forgetting; advances bkt._step

        self._recent.append(1.0 if correct else 0.0)
        self._steps += 1

        true_after = self.learner.known
        gained = float(true_after[node] - true_before[node])           # practice on served node
        gained = max(gained, 0.0)
        decayed_elsewhere = float(np.sum(np.maximum(true_before - true_after, 0.0))) - \
            max(true_before[node] - true_after[node], 0.0)
        newly_mastered = int(
            (true_before[node] < cfg.mastered_threshold) and
            (true_after[node] >= cfg.mastered_threshold)
        )

        reward = (
            cfg.progress_scale * gained
            + cfg.newly_mastered_bonus * newly_mastered
            - cfg.decay_penalty_scale * decayed_elsewhere
            - cfg.step_cost
        )
        if was_mastered and true_after[node] >= cfg.mastered_threshold:
            reward -= cfg.wasted_rep_penalty

        true_frac = self.learner.true_fraction_mastered(cfg.mastered_threshold)
        est_frac = self.bkt.n_mastered() / cfg.n_nodes
        done = true_frac >= 1.0 or self._steps >= cfg.max_steps
        if done:
            reward += cfg.terminal_scale * true_frac

        info = {
            "event": "mastered" if newly_mastered else ("correct" if correct else "wrong"),
            "correct": correct,
            "newly_mastered": newly_mastered,
            "frac_mastered": est_frac,          # what production would show
            "true_frac_mastered": true_frac,    # ground truth, used for reward
            "learner_type": self.learner.type_name,
            "steps": self._steps,
        }
        return self._state(), float(reward), bool(done), info

    # -- state assembly -----------------------------------------------
    def _node_block(self) -> np.ndarray:
        g, bkt = self.graph, self.bkt
        prereq_ok = self.valid_action_mask().astype(np.float32)
        block = np.stack([
            bkt.mastery,                                   # 1 estimate
            bkt.confidence(),                              # 2 evidence so far
            (bkt.attempts / (bkt.attempts + 3.0)).astype(np.float32),  # 3 attempt volume
            bkt.steps_since_seen(),                        # 4 recency
            prereq_ok,                                     # 5 unlocked?
            g.difficulty.astype(np.float32),               # 6 static
            (g.depth / max(g.max_depth, 1)).astype(np.float32),        # 7 static
            (g.n_descendants / max(g.n_nodes - 1, 1)).astype(np.float32),  # 8 hub-ness
        ], axis=1)                                         # (n_nodes, PER_NODE)
        return block.reshape(-1)

    def _global_block(self) -> np.ndarray:
        bkt, cfg = self.bkt, self.cfg
        recent_acc = float(np.mean(self._recent)) if self._recent else 0.0
        return np.array([
            float(np.mean(bkt.mastery)),
            bkt.n_mastered() / cfg.n_nodes,
            self._steps / max(cfg.max_steps, 1),
            recent_acc,
            float(np.mean(self.valid_action_mask())),
        ], dtype=np.float32)

    def _state(self) -> np.ndarray:
        return np.concatenate([self._node_block(), self._global_block()]).astype(np.float32)

    # -- helper used by the greedy baseline --------------------------
    def expected_delta(self, node_id: int) -> float:
        """Expected one-step mastery gain on ``node_id`` under the current
        estimate. Myopic on purpose - this is what the greedy baseline uses."""
        cfg = self.cfg
        m = float(self.bkt.mastery[node_id])
        p = m * (1 - cfg.p_slip) + (1 - m) * cfg.p_guess

        def bkt_step(prior, correct):
            if correct:
                num = prior * (1 - cfg.p_slip)
                den = num + (1 - prior) * cfg.p_guess
            else:
                num = prior * cfg.p_slip
                den = num + (1 - prior) * (1 - cfg.p_guess)
            post = num / max(den, 1e-6)
            return post + (1 - post) * cfg.p_learn

        exp_m = p * bkt_step(m, True) + (1 - p) * bkt_step(m, False)
        return exp_m - m
