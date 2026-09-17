"""All tunables in one place.

Keep this the *only* place hyperparameters live. `train()` logs a copy of the
Config next to every checkpoint so a run is always reproducible.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class Config:
    seed: int = 0

    # -- course / graph --------------------------------------------------
    # n_nodes is fixed across every graph (train and eval) because the DQN's
    # input layer is sized from it. Only the *structure* and difficulty of
    # each graph varies. When the real LearningPathGraph lands, it must also
    # report this many nodes (or you retrain).
    n_nodes: int = 12
    n_layers: int = 4
    topic_dim: int = 4
    train_graphs: int = 3          # episodes sample uniformly from this many graphs
    eval_graphs: int = 4          # held-out graphs the agent never trained on

    # -- environment dynamics -----------------------------------------
    max_steps: int = 60           # hard session budget: what makes ordering matter
    decay_rate: float = 0.012     # per-step multiplicative forgetting on untouched nodes
    prereq_mastery_required: float = 0.70
    mastered_threshold: float = 0.90

    # -- reward shaping ---------------------------------------------------
    progress_scale: float = 1.0        # + per unit of mastery gained this step
    newly_mastered_bonus: float = 1.0  # + each time a node crosses mastered_threshold
    decay_penalty_scale: float = 0.5   # - per unit of mastery lost to decay elsewhere
    step_cost: float = 0.02            # - every step (budget pressure)
    wasted_rep_penalty: float = 0.10   # - drilling an already-mastered, non-decayed node
    terminal_scale: float = 5.0        # * fraction of nodes mastered at episode end

    # -- BKT (the *estimator* the agent sees) --------------------------
    p_guess: float = 0.20
    p_slip: float = 0.10
    p_learn: float = 0.15
    starting_mastery_low: float = 0.05
    starting_mastery_high: float = 0.20

    # -- DQN agent --------------------------------------------------
    lr: float = 1e-3
    gamma: float = 0.98
    hidden: int = 128
    batch_size: int = 64
    buffer_capacity: int = 50_000
    epsilon_start: float = 1.0
    epsilon_end: float = 0.05
    epsilon_decay_steps: int = 8_000
    target_update_every: int = 400
    warmup_steps: int = 1_000
    grad_clip: float = 10.0

    # -- training loop -------------------------------------------------
    n_episodes: int = 4_000
    eval_every: int = 500
    eval_episodes: int = 300

    def to_dict(self) -> dict:
        return asdict(self)
