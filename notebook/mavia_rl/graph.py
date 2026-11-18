"""The course structure the agent routes over.

`LearningPathGraph` is the CONTRACT between the RL side (this package) and
your friend's learning-path module. As long as their object satisfies this
Protocol, nothing else in the package changes.

Two implementations ship here:

  * `FakeBranchingGraph` - a synthetic layered DAG with real branching, used
    for all development and training until the real graph exists.
  * `RealLearningPathGraph` - a stub. Fill it in from the Django models (or a
    JSON export of them) once the learning-path work is done.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class LearningPathGraph(Protocol):
    """Everything the environment needs to know about the course structure.

    All arrays are indexed by integer node id in ``range(n_nodes)``.
    """

    n_nodes: int
    difficulty: np.ndarray      # (n_nodes,) float in [0, 1]
    depth: np.ndarray           # (n_nodes,) int, 0 = entry node
    n_descendants: np.ndarray   # (n_nodes,) int - how many nodes depend on this one
    max_depth: int

    def prereqs(self, node_id: int) -> list[int]:
        """Node ids that must be mastered before ``node_id`` unlocks."""

    def entry_nodes(self) -> list[int]:
        """Nodes with no prerequisites."""

    def node_features(self, node_id: int) -> np.ndarray:
        """Static feature vector for a node (topic embedding etc.). Only used
        by future GNN-style agents; the current DQN reads difficulty / depth /
        descendants directly off the arrays above."""

    def valid_next(self, mastery: np.ndarray, prereq_required: float) -> np.ndarray:
        """Boolean mask over all nodes: True where every prerequisite has
        mastery >= ``prereq_required``. Mastered nodes stay selectable so the
        agent can choose to review against decay."""


# ---------------------------------------------------------------------------
# synthetic graph
# ---------------------------------------------------------------------------

def _count_descendants(prereqs: dict[int, list[int]], n_nodes: int) -> np.ndarray:
    """For each node, how many other nodes (transitively) list it as a
    prerequisite. High = a hub; getting it wrong strands a lot of the course."""
    # children[p] = nodes that directly require p
    children: dict[int, list[int]] = {i: [] for i in range(n_nodes)}
    for node, ps in prereqs.items():
        for p in ps:
            children[p].append(node)

    out = np.zeros(n_nodes, dtype=np.int64)
    for start in range(n_nodes):
        seen: set[int] = set()
        stack = list(children[start])
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(children[cur])
        out[start] = len(seen)
    return out


class FakeBranchingGraph:
    """A random layered DAG. Nodes in layer L may depend on 1-2 nodes in
    layer L-1, so the graph genuinely branches: some entry nodes feed many
    downstream topics, others feed one. That asymmetry is what a fixed
    "lowest mastery first" rule cannot exploit and a learned policy can.
    """

    def __init__(self, n_nodes: int = 12, n_layers: int = 4, topic_dim: int = 4,
                 seed: int = 0):
        rng = np.random.default_rng(seed)
        self.n_nodes = n_nodes

        layer_of = np.zeros(n_nodes, dtype=np.int64)
        for li, chunk in enumerate(np.array_split(np.arange(n_nodes), n_layers)):
            layer_of[chunk] = li
        self.depth = layer_of
        self.max_depth = int(layer_of.max())

        self._prereqs: dict[int, list[int]] = {i: [] for i in range(n_nodes)}
        for i in range(n_nodes):
            li = int(layer_of[i])
            if li == 0:
                continue
            prev = np.flatnonzero(layer_of == li - 1)
            k = int(rng.integers(1, min(2, len(prev)) + 1))
            self._prereqs[i] = sorted(int(x) for x in rng.choice(prev, size=k, replace=False))

        span = max(self.max_depth, 1)
        self.difficulty = np.clip(
            0.20 + 0.60 * (layer_of / span) + rng.normal(0.0, 0.05, n_nodes),
            0.05, 0.95,
        ).astype(np.float32)
        self.n_descendants = _count_descendants(self._prereqs, n_nodes)
        self._topic = rng.normal(0.0, 1.0, size=(n_nodes, topic_dim)).astype(np.float32)

    def prereqs(self, node_id: int) -> list[int]:
        return self._prereqs[node_id]

    def entry_nodes(self) -> list[int]:
        return [i for i, ps in self._prereqs.items() if not ps]

    def node_features(self, node_id: int) -> np.ndarray:
        return np.concatenate([
            [self.difficulty[node_id],
             self.depth[node_id] / max(self.max_depth, 1),
             self.n_descendants[node_id] / max(self.n_nodes - 1, 1)],
            self._topic[node_id],
        ]).astype(np.float32)

    def valid_next(self, mastery: np.ndarray, prereq_required: float) -> np.ndarray:
        mask = np.zeros(self.n_nodes, dtype=bool)
        for i in range(self.n_nodes):
            ps = self._prereqs[i]
            mask[i] = all(mastery[p] >= prereq_required for p in ps)
        return mask


# ---------------------------------------------------------------------------
# real graph - TO BE IMPLEMENTED by / with the learning-path work
# ---------------------------------------------------------------------------

class RealLearningPathGraph:
    """Adapter over the real learning-path structure.

    Fill this in once your friend's module exists. It must produce exactly the
    same interface as ``FakeBranchingGraph``. Suggested build path:

      1. Export the path graph to JSON: a list of
         ``{"id": int, "prereqs": [int], "difficulty": float, "depth": int}``
         (ids remapped to a dense 0..N-1 range).
      2. Load it here, compute ``n_descendants`` with ``_count_descendants``.
      3. If nodes carry a topic embedding / tag set, put it in ``node_features``;
         otherwise return ``difficulty/depth/descendants`` only.
      4. Keep ``n_nodes`` equal to ``Config.n_nodes`` or retrain the agent.

    ``valid_next`` can stay identical to the fake graph's (pure prereq gating),
    or, if the real paths are strictly followed edge-by-edge rather than
    "any unlocked node", restrict it to the successors of the current
    position - in which case add a ``position`` argument end to end.
    """

    def __init__(self, *_args, **_kwargs):
        raise NotImplementedError(
            "RealLearningPathGraph is a stub - see the class docstring. "
            "Train against FakeBranchingGraph until the real structure exists."
        )
