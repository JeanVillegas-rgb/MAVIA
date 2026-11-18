"""Bake-off harness: trained agent vs every baseline, on HELD-OUT graphs the
agent never trained on, broken down by hidden learner type.

Generalisation is the whole point - a policy that only wins on its training
graph has learned that graph, not a routing strategy.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np

from .agent import DQNAgent
from .baselines import ALL_BASELINES
from .config import Config
from .env import MaviaEnv
from .graph import FakeBranchingGraph


def make_eval_envs(cfg: Config) -> list[MaviaEnv]:
    return [
        MaviaEnv(FakeBranchingGraph(cfg.n_nodes, cfg.n_layers, cfg.topic_dim,
                                    seed=cfg.seed + 1000 + i), cfg)
        for i in range(cfg.eval_graphs)
    ]


def _run_episode(env: MaviaEnv, policy, seed: int) -> dict:
    env.reset(seed=seed)
    total_r = 0.0
    info: dict = {}
    for _ in range(env.cfg.max_steps):
        a = policy(env)
        _, r, done, info = env.step(a)
        total_r += r
        if done:
            break
    return {
        "reward": total_r,
        "frac_mastered": info.get("frac_mastered", 0.0),
        "true_frac_mastered": info.get("true_frac_mastered", 0.0),
        "steps": info.get("steps", env.cfg.max_steps),
        "learner_type": info.get("learner_type", "?"),
    }


def evaluate_policy(envs: list[MaviaEnv], policy, n_episodes: int,
                    base_seed: int) -> dict:
    rows = []
    for k in range(n_episodes):
        env = envs[k % len(envs)]
        rows.append(_run_episode(env, policy, seed=base_seed + k))
    by_type: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_type[row["learner_type"]].append(row["true_frac_mastered"])
    return {
        "reward_mean": float(np.mean([r["reward"] for r in rows])),
        "reward_std": float(np.std([r["reward"] for r in rows])),
        "frac_mastered_mean": float(np.mean([r["frac_mastered"] for r in rows])),
        "true_frac_mastered_mean": float(np.mean([r["true_frac_mastered"] for r in rows])),
        "steps_mean": float(np.mean([r["steps"] for r in rows])),
        "by_learner_type": {t: float(np.mean(v)) for t, v in sorted(by_type.items())},
    }


def bake_off(agent: DQNAgent, cfg: Config, n_episodes: int | None = None,
             verbose: bool = True) -> dict:
    n_episodes = n_episodes or cfg.eval_episodes
    envs = make_eval_envs(cfg)

    def trained(env: MaviaEnv) -> int:
        return agent.select_action(env._state(), env.valid_action_mask(), greedy=True)

    policies = [("trained DQN", trained)] + [(b.name, b) for b in ALL_BASELINES]
    results = {name: evaluate_policy(envs, pol, n_episodes, base_seed=cfg.seed + 500_000)
               for name, pol in policies}

    if verbose:
        print(f"\n{'policy':<28} | {'reward':>14} | {'est.mastered':>12} | "
              f"{'true.mastered':>13} | {'steps':>6}")
        print("-" * 88)
        for name, r in results.items():
            print(f"{name:<28} | {r['reward_mean']:7.2f} ± {r['reward_std']:4.1f} | "
                  f"{r['frac_mastered_mean']:12.3f} | {r['true_frac_mastered_mean']:13.3f} | "
                  f"{r['steps_mean']:6.1f}")
        print("\ntrue fraction mastered by hidden learner type:")
        types = sorted({t for r in results.values() for t in r["by_learner_type"]})
        print(f"{'policy':<28} | " + " | ".join(f"{t:>16}" for t in types))
        print("-" * (30 + 19 * len(types)))
        for name, r in results.items():
            cells = " | ".join(f"{r['by_learner_type'].get(t, float('nan')):16.3f}" for t in types)
            print(f"{name:<28} | {cells}")

    return results
