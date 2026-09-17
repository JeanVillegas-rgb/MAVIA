"""Simulator sanity checks - does the ENVIRONMENT behave like a learning
environment, independent of any agent? Run before trusting training results.

``python -m mavia_rl.validate``
"""

from __future__ import annotations

import numpy as np

from .baselines import GreedyLookahead, RandomPolicy
from .config import Config
from .env import MaviaEnv
from .graph import FakeBranchingGraph
from .learner import LEARNER_TYPES, LearnerSim

_results: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    _results.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}")


def _env(seed: int = 0) -> MaviaEnv:
    cfg = Config()
    return MaviaEnv(FakeBranchingGraph(cfg.n_nodes, cfg.n_layers, cfg.topic_dim, seed=seed), cfg)


def run() -> bool:
    cfg = Config()

    # 1. prereq gating
    env = _env(); env.reset(seed=1)
    env.bkt.mastery[:] = 0.0
    mask = env.valid_action_mask()
    locked = [i for i in range(cfg.n_nodes) if env.graph.prereqs(i)]
    check("nodes with unmet prereqs are locked", all(not mask[i] for i in locked))
    check("entry nodes are always unlocked",
          all(mask[i] for i in env.graph.entry_nodes()))

    # 2. satisfying prereqs unlocks
    env = _env(); env.reset(seed=2)
    target = next(i for i in range(cfg.n_nodes) if env.graph.prereqs(i))
    for p in env.graph.prereqs(target):
        env.bkt.mastery[p] = 0.95
    check("node unlocks once its prereqs are mastered",
          env.valid_action_mask()[target])

    # 3. BKT monotonicity
    env = _env(); env.reset(seed=3)
    m0 = env.bkt.mastery[0]
    env.bkt.observe(0, correct=True)
    check("a correct answer raises the mastery estimate", env.bkt.mastery[0] > m0)
    env.bkt.mastery[0] = 0.8
    env.bkt.observe(0, correct=False)
    check("a wrong answer lowers it from a high prior", env.bkt.mastery[0] < 0.8)

    # 4. forgetting
    env = _env(); env.reset(seed=4)
    env.bkt.mastery[:] = 0.8
    lost = env.bkt.decay_untouched(touched_node=0)
    check("untouched nodes lose mastery to decay", np.all(lost[1:] > 0))
    check("the touched node does not decay", lost[0] == 0)

    # 5. a competent policy nets positive reward over a full episode
    env = _env()
    totals = []
    for k in range(40):
        env.reset(seed=500 + k)
        tot = 0.0
        for _ in range(cfg.max_steps):
            _, r, done, _ = env.step(GreedyLookahead()(env))
            tot += r
            if done:
                break
        totals.append(tot)
    check(f"greedy nets positive episode reward (mean={np.mean(totals):.2f})",
          np.mean(totals) > 0)

    # 6. state vector well-formed
    env = _env(); s = env.reset(seed=6)
    from .env import state_dim
    check("state has the declared dimension", s.shape == (state_dim(cfg),))
    check("state is finite", bool(np.all(np.isfinite(s))))

    # 7. learner types actually differ
    env = _env(); rng = np.random.default_rng(0)
    outcomes: dict[str, float] = {}
    for t in LEARNER_TYPES:
        sim = LearnerSim({t: LEARNER_TYPES[t]})
        e = MaviaEnv(env.graph, cfg, learner=sim)
        fracs = []
        for k in range(40):
            e.reset(seed=100 + k)
            for _ in range(cfg.max_steps):
                _, _, done, info = e.step(GreedyLookahead()(e))
                if done:
                    break
            fracs.append(info["true_frac_mastered"])
        outcomes[t] = float(np.mean(fracs))
    spread = max(outcomes.values()) - min(outcomes.values())
    check(f"hidden learner types produce different outcomes (spread={spread:.2f})",
          spread > 0.05)

    # 8. greedy beats random on the simulator
    env = _env()
    def avg(policy):
        out = []
        for k in range(60):
            env.reset(seed=300 + k)
            tot = 0.0
            for _ in range(cfg.max_steps):
                _, r, done, _ = env.step(policy(env))
                tot += r
                if done:
                    break
            out.append(tot)
        return float(np.mean(out))
    check("greedy baseline beats random on the simulator",
          avg(GreedyLookahead()) > avg(RandomPolicy()))

    n_pass = sum(ok for _, ok in _results)
    print(f"\n{n_pass}/{len(_results)} checks passed")
    if n_pass < len(_results):
        print("Fix failing checks before trusting anything trained on this simulator.")
    return n_pass == len(_results)


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
