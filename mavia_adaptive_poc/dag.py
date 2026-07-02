"""Concept DAG: nodes and prerequisite edges for the adaptive engine POC.

Domain is an elementary science unit on matter, standing in for whatever
subject MAVIA is deployed on. Swap NODES/PREREQUISITES for a real curriculum
export.
"""

NODES = [
    "properties_of_matter",
    "mass_and_volume",
    "density",
    "states_of_matter",
    "changes_of_state",
    "mixtures_and_solutions",
    "physical_changes",
    "chemical_changes",
]

PREREQUISITES = {
    "properties_of_matter": [],
    "mass_and_volume": ["properties_of_matter"],
    "density": ["mass_and_volume"],
    "states_of_matter": [],
    "changes_of_state": ["density", "states_of_matter"],
    "mixtures_and_solutions": ["changes_of_state"],
    "physical_changes": ["mixtures_and_solutions"],
    "chemical_changes": ["mixtures_and_solutions"],
}

NODE_INDEX = {node: i for i, node in enumerate(NODES)}
NUM_NODES = len(NODES)


def prerequisites_met(node, mastered_set):
    return all(p in mastered_set for p in PREREQUISITES[node])


def unlocked_nodes(mastered_set):
    """Nodes with satisfied prerequisites that are not yet mastered (Box 1 gating)."""
    return [n for n in NODES if n not in mastered_set and prerequisites_met(n, mastered_set)]


def weakest_prerequisite(node, mastery_state):
    """Used by the Prerequisite Remediation side branch."""
    prereqs = PREREQUISITES[node]
    if not prereqs:
        return None
    return min(prereqs, key=lambda p: mastery_state.get(p, 0.0))
