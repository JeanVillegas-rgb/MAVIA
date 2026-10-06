// Pure helpers behind the learning path graph: what to draw, where, and what a
// drop means. Nothing here renders or calls the server, so it is tested alone.
import dagre from "@dagrejs/dagre";

export const NODE_WIDTH = 210;
export const NODE_HEIGHT = 64;
const STRIP_GAP = 110;
const STRIP_COLUMNS = 5;
const COLUMN_STEP = NODE_WIDTH + 30;
const ROW_STEP = NODE_HEIGHT + 30;
const MARGIN = 20;
// Room left of the concepts for each tier's name, and how far a tier's band
// reaches above and below its row.
const TIER_GUTTER = 96;
const TIER_PAD = 18;
// Links that skip a tier run down lanes to the right of the bands, so they
// never pass through a tier they do not belong to.
const LANE_GAP = 26;
const EDGE_COLOR = "#6b7a8c";

const byPosition = (steps) => [...steps].sort((a, b) => a.position - b.position);

// Links that shape the path, one per "learn first" entry on a step. A link a
// longer chain already implies (Matter -> Liquid beside Matter -> Solid -> Liquid)
// stays stored but is not drawn.
export function pathEdges(steps) {
  return steps.flatMap((step) =>
    (step.prerequisites || [])
      .filter((link) => !link.redundant)
      .map((link) => ({ linkId: link.link_id, from: link.concept_id, to: step.concept_id })),
  );
}

// Concepts in the prerequisite graph, and those no link touches yet.
export function splitLinked(steps) {
  const touched = new Set();
  for (const edge of pathEdges(steps)) {
    touched.add(edge.from);
    touched.add(edge.to);
  }
  const ordered = byPosition(steps);
  return {
    linked: ordered.filter((step) => touched.has(step.concept_id)),
    unlinked: ordered.filter((step) => !touched.has(step.concept_id)),
  };
}

// Selecting a concept shows what must come before it and what builds on it.
export function highlightRoles(steps, selectedId) {
  const roles = new Map();
  if (selectedId === null || selectedId === undefined) return roles;
  for (const edge of pathEdges(steps)) {
    if (edge.to === selectedId) roles.set(edge.from, "is-needed");
    if (edge.from === selectedId) roles.set(edge.to, "is-leads");
  }
  roles.set(selectedId, "is-selected");
  return roles;
}

export function buildGraph(steps, selectedId = null) {
  const { linked, unlinked } = splitLinked(steps);
  const edges = pathEdges(steps);
  const roles = highlightRoles(steps, selectedId);
  const hasSelection = selectedId !== null && selectedId !== undefined;
  const roleOf = (id) => roles.get(id) || (hasSelection ? "is-dimmed" : "");

  // dagre returns centres; React Flow positions are top-left corners.
  const positions = new Map();
  const tiers = [];
  const tierOf = new Map();
  let laneStart = 0;
  let bottom = 0;
  if (linked.length) {
    const graph = new dagre.graphlib.Graph();
    graph.setGraph({ rankdir: "TB", nodesep: 50, ranksep: 110, marginx: MARGIN, marginy: MARGIN });
    graph.setDefaultEdgeLabel(() => ({}));
    for (const step of linked) graph.setNode(String(step.concept_id), { width: NODE_WIDTH, height: NODE_HEIGHT });
    for (const edge of edges) graph.setEdge(String(edge.from), String(edge.to));
    dagre.layout(graph);
    const rows = new Set();
    for (const step of linked) {
      const { x, y } = graph.node(String(step.concept_id));
      positions.set(step.concept_id, { x: TIER_GUTTER + x - NODE_WIDTH / 2, y: y - NODE_HEIGHT / 2 });
      rows.add(y);
      bottom = Math.max(bottom, y + NODE_HEIGHT / 2);
    }
    // dagre puts every concept of one rank on the same row, so a tier is
    // simply a row: everything in it can be taught once the rows above are.
    const width = TIER_GUTTER + graph.graph().width;
    laneStart = width;
    const rowNumber = new Map([...rows].sort((a, b) => a - b).map((y, index) => [y, index + 1]));
    for (const step of linked) tierOf.set(step.concept_id, rowNumber.get(graph.node(String(step.concept_id)).y));
    [...rows].sort((a, b) => a - b).forEach((y, index) => {
      tiers.push({
        id: `tier-${index + 1}`,
        type: "tier",
        position: { x: 0, y: y - NODE_HEIGHT / 2 - TIER_PAD },
        data: { number: index + 1, width, height: NODE_HEIGHT + TIER_PAD * 2 },
        draggable: false,
        selectable: false,
        // Behind the concepts, and clicks pass through to the canvas so a
        // click on empty band still clears the selection.
        zIndex: -1,
        style: { pointerEvents: "none" },
      });
    });
  }

  const stripTop = linked.length ? bottom + STRIP_GAP : MARGIN + 34;
  unlinked.forEach((step, index) => {
    positions.set(step.concept_id, {
      x: MARGIN + (index % STRIP_COLUMNS) * COLUMN_STEP,
      y: stripTop + Math.floor(index / STRIP_COLUMNS) * ROW_STEP,
    });
  });

  const nodes = [...tiers, ...byPosition(steps).map((step) => ({
    id: String(step.concept_id),
    type: "concept",
    position: positions.get(step.concept_id),
    data: { step, role: roleOf(step.concept_id), pending: (step.suggestions || []).length },
  }))];
  const skipping = edges.filter((edge) => tierOf.get(edge.to) - tierOf.get(edge.from) > 1).length;
  if (skipping) {
    // The view fits itself to nodes, not lines; an invisible point at the
    // last lane keeps the lanes inside it.
    nodes.push({
      id: "lane-spacer",
      type: "spacer",
      position: { x: laneStart + (skipping + 1) * LANE_GAP, y: 0 },
      data: {},
      draggable: false,
      selectable: false,
      style: { pointerEvents: "none" },
    });
  }
  if (unlinked.length) {
    nodes.push({
      id: "not-linked-label",
      type: "label",
      position: { x: MARGIN, y: stripTop - 34 },
      data: { text: "Not linked yet" },
      draggable: false,
      selectable: false,
    });
  }

  let lanes = 0;
  return {
    nodes,
    edges: edges.map((edge) => {
      const common = {
        id: `link-${edge.linkId}`,
        source: String(edge.from),
        target: String(edge.to),
        markerEnd: { type: "arrowclosed", width: 18, height: 18, color: EDGE_COLOR },
        style: { stroke: EDGE_COLOR, strokeWidth: 1.5 },
      };
      if (tierOf.get(edge.to) - tierOf.get(edge.from) > 1) {
        // Out of the right side of the prerequisite, down its own lane, and
        // into the right side of the concept that needs it.
        lanes += 1;
        return {
          ...common,
          type: "lane",
          sourceHandle: "lane-out",
          targetHandle: "lane-in",
          data: { laneX: laneStart + lanes * LANE_GAP },
        };
      }
      // Right-angle lines run down the gaps between tiers instead of cutting
      // diagonally across the concepts in between.
      return { ...common, type: "smoothstep", pathOptions: { borderRadius: 10 } };
    }),
  };
}

// How many concepts carry recommendations the teacher still has to decide.
export function conceptsToReview(steps) {
  return steps.filter((step) => (step.suggestions || []).length > 0).length;
}

// Dropping B (dragged) onto A (target) means "teach A before B".
export function classifyDrop(steps, draggedId, targetId) {
  if (draggedId === targetId) return { kind: "self", current: [] };
  const dragged = steps.find((step) => step.concept_id === draggedId);
  const current = dragged?.prerequisites || [];
  if (current.some((entry) => entry.concept_id === targetId)) return { kind: "already", current };
  if (!current.length) return { kind: "add", current };
  return { kind: "choose", current };
}
