"""Simulated learner: stands in for a real student's responses.

Each concept has a hidden 'true ability' in [0, 1]. Response correctness is
drawn using the same guess/slip noise BKT assumes, so the simulation is a
fair stress test of the BKT update rather than a rigged demo.
"""

import random

from bkt import P_GUESS, P_SLIP
from dag import NODES


def random_true_ability(seed=None):
    rng = random.Random(seed)
    return {node: rng.uniform(0.1, 0.95) for node in NODES}


def simulate_response(node, true_ability, rng):
    ability = true_ability[node]
    p_correct = ability * (1 - P_SLIP) + (1 - ability) * P_GUESS
    return rng.random() < p_correct
