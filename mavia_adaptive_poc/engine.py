"""Adaptive engine orchestrator: wires DAG gating (Box 1), Bloom tiers
(Box 6), BKT updates (Box 5), and tier routing (Box 7) into one session loop.
"""

import random

from bkt import update_mastery
from bloom import QUESTION_TYPE, TIER_NAMES, route_for_mastery
from dag import unlocked_nodes, weakest_prerequisite

MIN_TIER, MAX_TIER = 1, 3
MASTERED_THRESHOLD = 0.95
STUCK_ESCALATIONS_TO_MASTER = 1  # escalating out of MAX_TIER marks the node mastered


class LearnerState:
    """Per-session tracking: mastery estimate and current Bloom tier per node."""

    def __init__(self):
        self.mastery = {}
        self.tier = {}
        self.mastered = set()

    def mastery_of(self, node):
        return self.mastery.get(node, 0.0)

    def tier_of(self, node):
        return self.tier.get(node, MIN_TIER)


def pick_next_node(state, mastered_set):
    """Box 2: choose which unlocked node to serve next.

    Prefers a node whose weakest prerequisite is shakiest, falling back to
    the first unlocked node — a stand-in for a real item-selection policy.
    """
    candidates = unlocked_nodes(mastered_set)
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda n: state.mastery_of(weakest_prerequisite(n, state.mastery) or n),
    )


def run_step(node, state, true_ability, rng):
    """One question cycle for `node`: present, respond, update, route."""
    tier = state.tier_of(node)
    q_type = QUESTION_TYPE[tier]

    correct = simulate(node, true_ability, rng)

    prior = state.mastery_of(node)
    posterior = update_mastery(prior, correct)
    state.mastery[node] = posterior

    decision = route_for_mastery(posterior)
    became_mastered = apply_routing(node, tier, decision, state)

    return {
        "node": node,
        "tier": tier,
        "tier_name": TIER_NAMES[tier],
        "question_type": q_type,
        "correct": correct,
        "prior": prior,
        "posterior": posterior,
        "decision": decision,
        "mastered": became_mastered,
    }


def simulate(node, true_ability, rng):
    from student_sim import simulate_response

    return simulate_response(node, true_ability, rng)


def apply_routing(node, tier, decision, state):
    """Box 7 -> Box 8: move the node's tier, or graduate it out of the DAG."""
    if decision == "escalate":
        if tier >= MAX_TIER:
            state.mastered.add(node)
            return True
        state.tier[node] = tier + 1
    elif decision == "deescalate":
        state.tier[node] = max(MIN_TIER, tier - 1)
    # "stagnate" leaves the tier unchanged
    return False


def run_session(seed=0, max_steps=200):
    from student_sim import random_true_ability

    rng = random.Random(seed)
    true_ability = random_true_ability(seed)
    state = LearnerState()
    trace = []

    for _ in range(max_steps):
        node = pick_next_node(state, state.mastered)
        if node is None:
            break
        trace.append(run_step(node, state, true_ability, rng))

    return state, trace


if __name__ == "__main__":
    from dag import NODES

    state, trace = run_session(seed=42)

    for i, step in enumerate(trace, 1):
        mark = "[MASTERED]" if step["mastered"] else ""
        print(
            f"{i:3d}. {step['node']:22s} tier={step['tier']} ({step['tier_name']:10s}) "
            f"{'correct  ' if step['correct'] else 'incorrect'} "
            f"mastery {step['prior']:.2f}->{step['posterior']:.2f} "
            f"-> {step['decision']:10s} {mark}"
        )

    print(f"\n{len(trace)} questions asked across {len(NODES)} concepts.")
    print(f"Mastered: {sorted(state.mastered)}")
    remaining = [n for n in NODES if n not in state.mastered]
    print(f"Not mastered: {remaining}")
