"""Kahn's algorithm with deterministic tie-breaking.

Pure Python and dependency-free on purpose: the ordering rule is the part of
this milestone that has to be defensible, so it is testable without a database.
"""

import heapq


class GraphCycleError(ValueError):
    """Raised when the prerequisite graph is not a DAG."""

    def __init__(self, unresolved_nodes):
        self.unresolved_nodes = sorted(unresolved_nodes)
        super().__init__(
            "Prerequisite graph contains a cycle; "
            f"{len(self.unresolved_nodes)} node(s) never reached in-degree zero: "
            f"{self.unresolved_nodes}"
        )


def build_adjacency(nodes, edges):
    """Return (adjacency list, in-degree map) for Kahn's algorithm.

    ``edges`` are ``(prerequisite, dependent)`` pairs. Duplicate pairs collapse,
    so a pair supported by two different text signals still counts once toward
    the dependent's in-degree.
    """
    node_set = set(nodes)
    adjacency = {node: [] for node in nodes}
    in_degree = {node: 0 for node in nodes}

    seen = set()
    for prerequisite, dependent in edges:
        if prerequisite not in node_set or dependent not in node_set:
            continue
        if prerequisite == dependent or (prerequisite, dependent) in seen:
            continue
        seen.add((prerequisite, dependent))
        adjacency[prerequisite].append(dependent)
        in_degree[dependent] += 1

    for successors in adjacency.values():
        successors.sort()
    return adjacency, in_degree


def mean_incoming_confidence(nodes, edges, edge_weights):
    """Average confidence of the edges pointing INTO each node.

    The mean, not the sum: a sum would just re-measure how many prerequisites a
    node has, which is already its own tie-breaker. The mean answers a different
    question — *how well evidenced* is this node's position in the graph.
    A node reached by a definitional reference scores higher than one attached
    by a weak co-occurrence guess, independently of how many edges each has.

    Nodes with no prerequisites score 1.0: a root is not a weakly-supported
    node, it is a node that needs no support.
    """
    totals = {node: [] for node in nodes}
    seen = set()
    for prerequisite, dependent in edges:
        if dependent not in totals or prerequisite not in totals:
            continue
        if (prerequisite, dependent) in seen or prerequisite == dependent:
            continue
        seen.add((prerequisite, dependent))
        totals[dependent].append(edge_weights.get((prerequisite, dependent), 1.0))
    return {
        node: (sum(weights) / len(weights)) if weights else 1.0
        for node, weights in totals.items()
    }


def kahn_topological_order(nodes, edges, first_mention_rank=None, edge_weights=None):
    """Order ``nodes`` so every prerequisite precedes its dependents.

    The frontier holds exactly the nodes whose prerequisites are all satisfied —
    Kahn's, not a DFS-based sort. Because more than one node is usually ready at
    once, the frontier is drained in a fixed priority rather than arbitrary
    arrival order, which is what makes the path identical on every run:

    1. lower DAG depth first (teach foundations before anything built on them,
       which also regroups content into dependency layers)
    2. then higher mean incoming edge confidence — within one layer, teach the
       nodes whose prerequisite relationships are best evidenced before the ones
       attached by weaker signals, so a mis-derived edge does its damage late
       rather than early
    3. then earlier first mention in the source text
    4. then fewer prerequisites
    5. then object id, purely so the result is never ambiguous

    ``edge_weights`` maps ``(prerequisite, dependent)`` to that edge's
    confidence. Omit it and every edge counts as fully confident, which reduces
    rule 2 to a no-op and leaves the original ordering unchanged.

    Returns ``(ordered_nodes, depth_by_node)``.
    Raises :class:`GraphCycleError` if the graph is not acyclic.
    """
    ranks = first_mention_rank or {}
    weights = edge_weights or {}
    adjacency, in_degree = build_adjacency(nodes, edges)

    prerequisite_count = dict(in_degree)
    depth = {node: 0 for node in nodes}
    confidence = mean_incoming_confidence(nodes, edges, weights)

    def priority(node):
        return (
            depth[node],
            # negated: the heap pops the smallest, and we want the most
            # confident node first
            -confidence.get(node, 1.0),
            ranks.get(node, 0),
            prerequisite_count[node],
            node,
        )

    frontier = [priority(node) for node in nodes if in_degree[node] == 0]
    heapq.heapify(frontier)

    ordered = []
    while frontier:
        node = heapq.heappop(frontier)[-1]
        ordered.append(node)
        for dependent in adjacency[node]:
            # A node's depth is final once every prerequisite has been emitted,
            # so it is correct by the time the node enters the frontier.
            depth[dependent] = max(depth[dependent], depth[node] + 1)
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                heapq.heappush(frontier, priority(dependent))

    if len(ordered) != len(nodes):
        emitted = set(ordered)
        raise GraphCycleError([node for node in nodes if node not in emitted])

    return ordered, depth
