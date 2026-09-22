"""MAVIA adaptive-routing RL prototype (v3).

A DQN that chooses which course node to serve next, given a BKT mastery
estimate built from a student's interactions, over a learning-path graph.

The graph structure is behind the `LearningPathGraph` Protocol so your
friend's learning-path module can be dropped in without touching the RL code
(see `graph.RealLearningPathGraph`).

Quick start:

    from mavia_rl.config import Config
    from mavia_rl.train import train
    out = train(Config(n_episodes=2000))     # trains + prints held-out bake-off

    from mavia_rl.validate import run as validate
    validate()                                # simulator sanity checks
"""

from .config import Config
from .env import MaviaEnv, n_actions, state_dim
from .graph import FakeBranchingGraph, LearningPathGraph, RealLearningPathGraph
from .learner import LearnerSim
from .mastery import BKTMasteryModel

__all__ = [
    "Config", "MaviaEnv", "state_dim", "n_actions",
    "LearningPathGraph", "FakeBranchingGraph", "RealLearningPathGraph",
    "LearnerSim", "BKTMasteryModel",
]
