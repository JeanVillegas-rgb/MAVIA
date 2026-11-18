"""Non-learned policies the agent has to beat.

Random is a strawman - ignore it except as a sanity floor. The bar is
`GreedyLookahead` and `ThresholdHeuristic`: if the trained agent doesn't
clearly beat those on held-out graphs and learner types, RL isn't buying
you anything here yet and you should fix the environment / reward before
touching the agent.

Every policy is a callable ``policy(env) -> action``.
"""

from __future__ import annotations

import numpy as np

from .env import MaviaEnv


class RandomPolicy:
    name = "random (valid only)"

    def __call__(self, env: MaviaEnv) -> int:
        return int(np.random.choice(np.flatnonzero(env.valid_action_mask())))


class ThresholdHeuristic:
    """The original notebook's rule: serve the lowest-mastery unlocked node.
    No lookahead, no personalisation, no notion of hubs or decay."""
    name = "heuristic (lowest mastery)"

    def __call__(self, env: MaviaEnv) -> int:
        mask = env.valid_action_mask()
        m = np.where(mask, env.bkt.mastery, np.inf)
        return int(np.argmin(m))


class GreedyLookahead:
    """One-step optimal for immediate mastery gain. Strong, but myopic: it
    can't trade a slow start on a hub node for a big payoff later, and it
    can't read the learner type."""
    name = "greedy (1-step mastery gain)"

    def __call__(self, env: MaviaEnv) -> int:
        mask = env.valid_action_mask()
        deltas = np.array([env.expected_delta(i) if mask[i] else -np.inf
                           for i in range(env.cfg.n_nodes)])
        return int(np.argmax(deltas))


class SpacedRepetition:
    """Prioritise nodes that are both shaky and important (many descendants),
    and revisit mastered nodes that have started to decay."""
    name = "spaced-repetition"

    def __call__(self, env: MaviaEnv) -> int:
        mask = env.valid_action_mask()
        g, bkt, cfg = env.graph, env.bkt, env.cfg
        importance = 1.0 + g.n_descendants / max(g.n_nodes - 1, 1)
        risk = np.where(
            bkt.mastery >= cfg.mastered_threshold,
            bkt.steps_since_seen(),                 # decay risk on mastered nodes
            (1.0 - bkt.mastery),                    # still-to-learn gap
        )
        score = np.where(mask, risk * importance, -np.inf)
        return int(np.argmax(score))


ALL_BASELINES = [ThresholdHeuristic(), GreedyLookahead(), SpacedRepetition(), RandomPolicy()]
