"""Double-DQN agent with action masking.

Changes from the original notebook:
  * Double DQN target (online net picks the argmax, target net scores it) -
    cuts the overestimation bias that made the first prototype plateau.
  * `select_action` no longer mutates any counter, so calling it during
    evaluation doesn't move epsilon. Exploration is driven by `train_steps`,
    incremented only in `train_step`.
  * Smooth-L1 loss + gradient clipping for stability.
"""

from __future__ import annotations

import random
from collections import deque

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim

from .config import Config


class QNetwork(nn.Module):
    def __init__(self, state_dim: int, n_actions: int, hidden: int):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(state_dim, hidden), nn.ReLU(),
            nn.Linear(hidden, hidden), nn.ReLU(),
            nn.Linear(hidden, n_actions),
        )

    def forward(self, x):
        return self.net(x)


class ReplayBuffer:
    def __init__(self, capacity: int):
        self.buf: deque = deque(maxlen=capacity)

    def push(self, s, a, r, s2, done, mask2):
        self.buf.append((s, a, r, s2, done, mask2))

    def sample(self, batch_size: int):
        batch = random.sample(self.buf, batch_size)
        s, a, r, s2, d, m2 = zip(*batch)
        return (np.array(s, dtype=np.float32), np.array(a, dtype=np.int64),
                np.array(r, dtype=np.float32), np.array(s2, dtype=np.float32),
                np.array(d, dtype=np.float32), np.array(m2, dtype=bool))

    def __len__(self):
        return len(self.buf)


class DQNAgent:
    def __init__(self, state_dim: int, n_actions: int, cfg: Config):
        self.cfg = cfg
        self.n_actions = n_actions
        self.policy = QNetwork(state_dim, n_actions, cfg.hidden)
        self.target = QNetwork(state_dim, n_actions, cfg.hidden)
        self.target.load_state_dict(self.policy.state_dict())
        self.target.eval()
        self.opt = optim.Adam(self.policy.parameters(), lr=cfg.lr)
        self.replay = ReplayBuffer(cfg.buffer_capacity)
        self.train_steps = 0

    def epsilon(self) -> float:
        c = self.cfg
        frac = min(1.0, self.train_steps / max(c.epsilon_decay_steps, 1))
        return c.epsilon_start + frac * (c.epsilon_end - c.epsilon_start)

    @torch.no_grad()
    def select_action(self, state: np.ndarray, valid_mask: np.ndarray,
                      greedy: bool = False) -> int:
        valid_idx = np.flatnonzero(valid_mask)
        if not greedy and random.random() < self.epsilon():
            return int(np.random.choice(valid_idx))
        q = self.policy(torch.as_tensor(state, dtype=torch.float32).unsqueeze(0)).squeeze(0).numpy()
        q = np.where(valid_mask, q, -1e9)
        return int(np.argmax(q))

    def train_step(self):
        c = self.cfg
        if len(self.replay) < max(c.batch_size, c.warmup_steps):
            return None
        self.train_steps += 1

        s, a, r, s2, d, m2 = self.replay.sample(c.batch_size)
        s = torch.as_tensor(s); a = torch.as_tensor(a).unsqueeze(1)
        r = torch.as_tensor(r); s2 = torch.as_tensor(s2)
        d = torch.as_tensor(d); m2 = torch.as_tensor(m2)

        q = self.policy(s).gather(1, a).squeeze(1)
        with torch.no_grad():
            next_online = self.policy(s2).masked_fill(~m2, -1e9)
            next_act = next_online.argmax(1, keepdim=True)
            next_q = self.target(s2).gather(1, next_act).squeeze(1)
            target = r + c.gamma * next_q * (1 - d)

        loss = nn.functional.smooth_l1_loss(q, target)
        self.opt.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(self.policy.parameters(), c.grad_clip)
        self.opt.step()

        if self.train_steps % c.target_update_every == 0:
            self.target.load_state_dict(self.policy.state_dict())
        return float(loss.item())

    # -- persistence ------------------------------------------------
    def save(self, path):
        torch.save({"policy": self.policy.state_dict(),
                    "train_steps": self.train_steps}, path)

    def load(self, path):
        ckpt = torch.load(path, map_location="cpu")
        self.policy.load_state_dict(ckpt["policy"])
        self.target.load_state_dict(self.policy.state_dict())
        self.train_steps = ckpt.get("train_steps", 0)
