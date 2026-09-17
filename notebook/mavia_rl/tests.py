"""Fast unit tests. ``pytest mavia_rl/tests.py`` (or ``python -m mavia_rl.tests``)."""

from __future__ import annotations

import numpy as np

from .config import Config
from .env import MaviaEnv, n_actions, state_dim
from .graph import FakeBranchingGraph, LearningPathGraph, _count_descendants


def _graph(seed=0):
    c = Config()
    return FakeBranchingGraph(c.n_nodes, c.n_layers, c.topic_dim, seed=seed)


def test_graph_satisfies_protocol():
    assert isinstance(_graph(), LearningPathGraph)


def test_graph_is_a_dag_with_entry_nodes():
    g = _graph()
    assert g.entry_nodes(), "graph must have at least one prereq-free node"
    for i in range(g.n_nodes):
        assert i not in g.prereqs(i)
        for p in g.prereqs(i):
            assert g.depth[p] < g.depth[i], "prereq must sit in an earlier layer"


def test_descendants_count():
    prereqs = {0: [], 1: [0], 2: [1], 3: [0]}
    d = _count_descendants(prereqs, 4)
    assert d[0] == 3 and d[1] == 1 and d[2] == 0 and d[3] == 0


def test_state_and_action_dims():
    c = Config()
    env = MaviaEnv(_graph(), c)
    s = env.reset(seed=1)
    assert s.shape == (state_dim(c),)
    assert n_actions(c) == c.n_nodes
    assert np.all(np.isfinite(s))


def test_mask_blocks_locked_nodes():
    env = MaviaEnv(_graph(), Config())
    env.reset(seed=2)
    env.bkt.mastery[:] = 0.0
    mask = env.valid_action_mask()
    for i in range(env.cfg.n_nodes):
        if env.graph.prereqs(i):
            assert not mask[i]


def test_step_runs_and_rewards_are_finite():
    env = MaviaEnv(_graph(), Config())
    env.reset(seed=3)
    for _ in range(env.cfg.max_steps):
        a = int(np.random.choice(np.flatnonzero(env.valid_action_mask())))
        _, r, done, info = env.step(a)
        assert np.isfinite(r)
        assert set(info) >= {"event", "correct", "frac_mastered"}
        if done:
            break


def test_correct_answer_raises_estimate():
    env = MaviaEnv(_graph(), Config())
    env.reset(seed=4)
    before = float(env.bkt.mastery[0])
    env.bkt.observe(0, correct=True)
    assert env.bkt.mastery[0] > before


def test_episode_terminates_within_budget():
    env = MaviaEnv(_graph(), Config())
    env.reset(seed=5)
    steps = 0
    for _ in range(env.cfg.max_steps + 5):
        _, _, done, _ = env.step(int(np.argmax(env.valid_action_mask())))
        steps += 1
        if done:
            break
    assert steps <= env.cfg.max_steps


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"[PASS] {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
