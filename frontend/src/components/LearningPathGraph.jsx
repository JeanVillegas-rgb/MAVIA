import { useMemo } from "react";

// Layout constants. The graph is laid out deterministically in JS rather than
// measured from the DOM, so it renders identically on every machine and needs
// no layout library.
const NODE_W = 168;
const NODE_H = 58;
const H_GAP = 22;
const V_GAP = 78;
const SUB_GAP = 14;
const LABEL_W = 96;
const PAD = 12;

// A dependency layer can be very wide — one real lesson has 24 lessons sitting
// at the same depth, which laid out in a single line would be a 4,600px strip
// nobody can read. Wide layers wrap onto extra rows INSIDE their own band, so
// the band still says "all of this is available at once" without the scroll.
const MAX_COLS = 6;

function chunk(items, size) {
  const out = [];
  for (let index = 0; index < items.length; index += size) {
    out.push(items.slice(index, index + size));
  }
  return out;
}

/**
 * Group the path's steps into dependency layers.
 *
 * The layer IS the hierarchy: everything in layer 0 can be taught first,
 * everything in layer 1 needs something from an earlier layer, and so on. Two
 * nodes sharing a layer have no dependency between them — the numbered order
 * within a layer is the tie-break, not a requirement.
 */
function buildLayout(steps) {
  const byDepth = new Map();
  steps.forEach((step) => {
    const row = byDepth.get(step.dag_depth) || [];
    row.push(step);
    byDepth.set(step.dag_depth, row);
  });

  const depths = [...byDepth.keys()].sort((a, b) => a - b);
  const rows = depths.map((depth) =>
    byDepth.get(depth).slice().sort((a, b) => a.position - b.position)
  );
  const subRowsByDepth = rows.map((row) => chunk(row, MAX_COLS));

  const widest = subRowsByDepth.reduce(
    (max, subRows) =>
      subRows.reduce(
        (rowMax, sub) =>
          Math.max(rowMax, sub.length * NODE_W + (sub.length - 1) * H_GAP),
        max
      ),
    0
  );

  const positions = new Map();
  const bands = [];
  let cursor = PAD;

  subRowsByDepth.forEach((subRows, index) => {
    const contentHeight = subRows.length * NODE_H + (subRows.length - 1) * SUB_GAP;
    bands.push({
      depth: depths[index],
      top: cursor,
      height: contentHeight,
      count: rows[index].length,
    });

    subRows.forEach((sub, subIndex) => {
      const rowWidth = sub.length * NODE_W + (sub.length - 1) * H_GAP;
      const startX = LABEL_W + (widest - rowWidth) / 2;
      const y = cursor + subIndex * (NODE_H + SUB_GAP);
      sub.forEach((step, column) => {
        positions.set(step.learning_object_id, {
          step,
          x: startX + column * (NODE_W + H_GAP),
          y,
        });
      });
    });

    cursor += contentHeight + V_GAP;
  });

  return {
    positions,
    bands,
    layerCount: depths.length,
    width: LABEL_W + widest + PAD * 2,
    height: cursor - V_GAP + PAD,
  };
}

function truncate(text, limit) {
  const value = text || "(untitled)";
  return value.length > limit ? `${value.slice(0, limit - 1)}…` : value;
}

/**
 * A curve from the bottom of the prerequisite to the top of the dependent.
 * Control points sit vertically between the two so edges leave and enter the
 * boxes straight down/up, which keeps crossings readable.
 */
function edgePath(from, to) {
  const x1 = from.x + NODE_W / 2;
  const y1 = from.y + NODE_H;
  const x2 = to.x + NODE_W / 2;
  const y2 = to.y;
  const midpoint = y1 + (y2 - y1) / 2;
  return `M ${x1} ${y1} C ${x1} ${midpoint}, ${x2} ${midpoint}, ${x2} ${y2}`;
}

export default function LearningPathGraph({
  steps,
  edges,
  selectedId,
  onSelect,
}) {
  const layout = useMemo(() => buildLayout(steps), [steps]);

  const drawnEdges = useMemo(
    () =>
      (edges || [])
        .map((edge) => {
          const from = layout.positions.get(edge.prerequisite_id);
          const to = layout.positions.get(edge.dependent_id);
          if (!from || !to) return null;
          return { edge, d: edgePath(from, to) };
        })
        .filter(Boolean),
    [edges, layout]
  );

  if (!steps.length) return null;

  const isDimmed = (id) =>
    selectedId != null &&
    selectedId !== id &&
    !drawnEdges.some(
      ({ edge }) =>
        (edge.prerequisite_id === selectedId && edge.dependent_id === id) ||
        (edge.dependent_id === selectedId && edge.prerequisite_id === id)
    );

  return (
    <div className="learning-path-graph-scroll">
      <svg
        className="learning-path-graph"
        width={layout.width}
        height={layout.height}
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        role="img"
        aria-label={`Dependency graph: ${steps.length} lessons across ${layout.layerCount} layers`}
      >
        <defs>
          <marker
            id="lp-arrow"
            viewBox="0 0 8 8"
            refX="7"
            refY="4"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M 0 1 L 7 4 L 0 7 z" fill="#9aa4ad" />
          </marker>
        </defs>

        {/* Layer bands, drawn first so everything else sits on top. */}
        {layout.bands.map((band, index) => (
          <g key={`band-${band.depth}`}>
            <rect
              x={0}
              y={band.top - 14}
              width={layout.width}
              height={band.height + 28}
              rx={10}
              className={index % 2 === 0 ? "lp-band" : "lp-band is-alt"}
            />
            <text x={PAD} y={band.top + 18} className="lp-layer-label">
              Layer {band.depth}
            </text>
            <text x={PAD} y={band.top + 33} className="lp-layer-sub">
              {index === 0 ? "start here" : `${band.count} lesson${band.count === 1 ? "" : "s"}`}
            </text>
          </g>
        ))}

        {drawnEdges.map(({ edge, d }) => {
          const dim =
            selectedId != null &&
            edge.prerequisite_id !== selectedId &&
            edge.dependent_id !== selectedId;
          return (
            <path
              key={edge.id}
              d={d}
              className={[
                "lp-edge",
                edge.source === "teacher" ? "is-teacher" : "",
                dim ? "is-dim" : "",
              ]
                .filter(Boolean)
                .join(" ")}
              // Confidence is visible: a weakly-evidenced prerequisite is drawn
              // thinner and fainter than a definitional one.
              strokeWidth={0.9 + edge.weight * 1.6}
              strokeOpacity={dim ? 0.12 : 0.3 + edge.weight * 0.5}
              markerEnd="url(#lp-arrow)"
            >
              <title>
                {`${edge.prerequisite_title} → ${edge.dependent_title} (${edge.signal}, confidence ${edge.weight})`}
              </title>
            </path>
          );
        })}

        {[...layout.positions.values()].map(({ step, x, y }) => {
          const selected = selectedId === step.learning_object_id;
          return (
            <g
              key={step.learning_object_id}
              transform={`translate(${x} ${y})`}
              className={[
                "lp-node",
                selected ? "is-selected" : "",
                isDimmed(step.learning_object_id) ? "is-dim" : "",
                step.prerequisite_count === 0 ? "is-root" : "",
              ]
                .filter(Boolean)
                .join(" ")}
              tabIndex={0}
              role="button"
              aria-pressed={selected}
              onClick={() => onSelect(selected ? null : step.learning_object_id)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onSelect(selected ? null : step.learning_object_id);
                }
              }}
            >
              <title>
                {`Step ${step.position}: ${step.title}. ` +
                  (step.prerequisite_count === 0
                    ? "No prerequisites."
                    : `${step.prerequisite_count} prerequisite${
                        step.prerequisite_count === 1 ? "" : "s"
                      }.`)}
              </title>
              <rect width={NODE_W} height={NODE_H} rx={9} className="lp-node-box" />
              <circle cx={16} cy={16} r={10} className="lp-node-badge" />
              <text x={16} y={20} className="lp-node-position">
                {step.position}
              </text>
              <text x={32} y={21} className="lp-node-title">
                {truncate(step.title, 17)}
              </text>
              <text x={12} y={42} className="lp-node-meta">
                {step.prerequisite_count === 0
                  ? "no prerequisites"
                  : `after ${step.prerequisite_count} · was #${step.source_order + 1} in PDF`}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
