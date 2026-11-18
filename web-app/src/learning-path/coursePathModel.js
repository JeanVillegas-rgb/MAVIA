// Pure helpers behind the Course path page: where topics sit, how each arrow
// between them is drawn, and the learning order listed under the graph.
// Nothing here renders or calls the server.
import dagre from "@dagrejs/dagre";

export const TOPIC_WIDTH = 220;
export const TOPIC_HEIGHT = 60;
const STRIP_GAP = 110;
const STRIP_COLUMNS = 4;
const COLUMN_STEP = TOPIC_WIDTH + 40;
const ROW_STEP = TOPIC_HEIGHT + 30;
const MARGIN = 20;

// "contradicts" first: a teacher must see an arrow against the outline even
// when it also carries accepted links.
export function arrowKind(arrow) {
  if (arrow.contradicts_outline) return "contradicts";
  if (!arrow.shaping) return "pending";
  return "follows";
}

function arrowLabel(arrow) {
  if (arrow.shaping) return `${arrow.shaping} link${arrow.shaping === 1 ? "" : "s"}`;
  return `${arrow.pending} suggestion${arrow.pending === 1 ? "" : "s"}`;
}

const COLOURS = { follows: "#2f6f4f", pending: "#b8860b", contradicts: "#b3261e" };
const byPosition = (topics) => [...topics].sort((a, b) => a.position - b.position);

// Top to bottom like the topic graph: topics the arrows touch are laid out by
// dagre, the rest sit in a "Not linked yet" strip below.
export function buildCourseGraph(path, selectedArrow = null) {
  const topics = byPosition(path.topics);
  const ids = new Set(topics.map((topic) => topic.id));
  const arrows = path.arrows.filter((arrow) => ids.has(arrow.from_topic) && ids.has(arrow.to_topic));
  const touched = new Set(arrows.flatMap((arrow) => [arrow.from_topic, arrow.to_topic]));
  const linked = topics.filter((topic) => touched.has(topic.id));
  const unlinked = topics.filter((topic) => !touched.has(topic.id));

  const positions = new Map();
  let bottom = 0;
  if (linked.length) {
    const graph = new dagre.graphlib.Graph();
    graph.setGraph({ rankdir: "TB", nodesep: 40, ranksep: 70, marginx: MARGIN, marginy: MARGIN });
    graph.setDefaultEdgeLabel(() => ({}));
    for (const topic of linked) graph.setNode(String(topic.id), { width: TOPIC_WIDTH, height: TOPIC_HEIGHT });
    for (const arrow of arrows) graph.setEdge(String(arrow.from_topic), String(arrow.to_topic));
    dagre.layout(graph);
    for (const topic of linked) {
      const { x, y } = graph.node(String(topic.id));
      positions.set(topic.id, { x: x - TOPIC_WIDTH / 2, y: y - TOPIC_HEIGHT / 2 });
      bottom = Math.max(bottom, y + TOPIC_HEIGHT / 2);
    }
  }
  const stripTop = linked.length ? bottom + STRIP_GAP : MARGIN + 34;
  unlinked.forEach((topic, index) => {
    positions.set(topic.id, {
      x: MARGIN + (index % STRIP_COLUMNS) * COLUMN_STEP,
      y: stripTop + Math.floor(index / STRIP_COLUMNS) * ROW_STEP,
    });
  });

  const nodes = topics.map((topic) => ({
    id: String(topic.id),
    type: "topic",
    position: positions.get(topic.id),
    data: { topic, empty: !topic.has_content },
    draggable: false,
  }));
  if (unlinked.length) {
    nodes.push({
      id: "not-linked-label", type: "label", position: { x: MARGIN, y: stripTop - 34 },
      data: { text: "Not linked yet" }, draggable: false, selectable: false,
    });
  }
  const edges = arrows.map((arrow) => {
    const kind = arrowKind(arrow);
    const id = `${arrow.from_topic}-${arrow.to_topic}`;
    return {
      id,
      source: String(arrow.from_topic),
      target: String(arrow.to_topic),
      label: arrowLabel(arrow),
      className: `cp-arrow ${kind}${selectedArrow === id ? " selected" : ""}`,
      markerEnd: { type: "arrowclosed", width: 18, height: 18, color: COLOURS[kind] },
      style: { stroke: COLOURS[kind], strokeWidth: selectedArrow === id ? 3 : 1.5, strokeDasharray: kind === "follows" ? undefined : "6 4" },
      data: { arrow },
    };
  });
  return { nodes, edges };
}

const CONFIRMED = new Set(["accepted", "approved"]);
const byRank = (a, b) => (a.rank ?? Infinity) - (b.rank ?? Infinity) || a.id - b.id;

// The course as a learner goes through it: topics with content in outline
// order, each step with the earlier-topic links into it.
export function learningOrder(path) {
  const links = path.arrows.flatMap((arrow) => arrow.links);
  return byPosition(path.topics)
    .filter((topic) => topic.has_content)
    .map((topic) => ({
      topic,
      published: Boolean(topic.published),
      steps: (topic.steps || []).map((step) => {
        const into = links.filter((link) => link.dependent.concept_id === step.concept_id);
        return {
          ...step,
          revisit: into.filter((link) => CONFIRMED.has(link.status)).sort(byRank),
          suggestions: into.filter((link) => link.status === "pending").sort(byRank),
        };
      }),
    }));
}
