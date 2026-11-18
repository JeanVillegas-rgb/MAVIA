"""Training loop. Run as ``python -m mavia_rl.train`` or call ``train(cfg)``.

Trains on a pool of graphs (sampled per episode) so the policy sees varied
structure, and periodically runs the held-out bake-off from `evaluate.py`.
Every run writes ``runs/<timestamp>/`` with config, reward history, eval
history and the checkpoint.
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path

import numpy as np
import torch

from .agent import DQNAgent
from .config import Config
from .env import MaviaEnv, n_actions, state_dim
from .evaluate import bake_off
from .graph import FakeBranchingGraph

RUNS_DIR = Path(__file__).parent / "runs"


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def make_train_envs(cfg: Config) -> list[MaviaEnv]:
    return [
        MaviaEnv(FakeBranchingGraph(cfg.n_nodes, cfg.n_layers, cfg.topic_dim,
                                    seed=cfg.seed + i), cfg)
        for i in range(cfg.train_graphs)
    ]


def train(cfg: Config | None = None, *, save: bool = True) -> dict:
    cfg = cfg or Config()
    seed_everything(cfg.seed)

    envs = make_train_envs(cfg)
    agent = DQNAgent(state_dim(cfg), n_actions(cfg), cfg)
    rng = np.random.default_rng(cfg.seed)

    reward_history: list[float] = []
    eval_history: list[dict] = []
    t0 = time.time()

    for ep in range(cfg.n_episodes):
        env = envs[int(rng.integers(len(envs)))]
        state = env.reset(seed=cfg.seed + 10_000 + ep)
        ep_reward = 0.0

        for _ in range(cfg.max_steps):
            mask = env.valid_action_mask()
            action = agent.select_action(state, mask)
            next_state, reward, done, _ = env.step(action)
            agent.replay.push(state, action, reward, next_state, done,
                              env.valid_action_mask())
            agent.train_step()
            state = next_state
            ep_reward += reward
            if done:
                break

        reward_history.append(ep_reward)

        if (ep + 1) % cfg.eval_every == 0:
            res = bake_off(agent, cfg, n_episodes=max(100, cfg.eval_episodes // 3),
                           verbose=False)
            trained = res["trained DQN"]
            greedy = res["greedy (1-step mastery gain)"]
            eval_history.append({"episode": ep + 1, **{k: v for k, v in trained.items()
                                                       if not isinstance(v, dict)}})
            print(f"ep {ep+1:>5}/{cfg.n_episodes} | "
                  f"train reward (last {cfg.eval_every}): "
                  f"{np.mean(reward_history[-cfg.eval_every:]):6.2f} | "
                  f"eps {agent.epsilon():.3f} | "
                  f"eval: DQN true-mastered {trained['true_frac_mastered_mean']:.3f} "
                  f"vs greedy {greedy['true_frac_mastered_mean']:.3f} | "
                  f"DQN reward {trained['reward_mean']:.2f}")

    elapsed = time.time() - t0
    print(f"\ndone in {elapsed:.0f}s")

    print("\n=== FINAL BAKE-OFF (held-out graphs) ===")
    final = bake_off(agent, cfg, verbose=True)

    out = {
        "config": cfg.to_dict(),
        "reward_history": reward_history,
        "eval_history": eval_history,
        "final_bake_off": final,
        "elapsed_sec": elapsed,
    }

    if save:
        run_dir = RUNS_DIR / time.strftime("%Y%m%d-%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run.json").write_text(json.dumps(out, indent=2))
        agent.save(run_dir / "policy.pt")
        print(f"\nsaved -> {run_dir}")
        out["run_dir"] = str(run_dir)

    out["agent"] = agent
    return out


if __name__ == "__main__":
    train(Config())
