// The learning path drawn as a prerequisite graph. Layout comes from
// graphModel.buildGraph; dragging is only turned on for the editable screen,
// and a drop is reported upward -- nothing here changes links itself.
import { useEffect, useMemo } from "react";
import {
  BaseEdge,
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useNodesState,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { buildGraph } from "./graphModel";
import "./pathGraph.css";

function ConceptNode({ data }) {
  const { step, role, pending, showPending } = data;
  const title = step.title || "Untitled concept";
  const flagged = showPending && pending > 0;
  return (
    <div className={`pg-node ${role} ${flagged ? "has-pending" : ""}`.trim()} title={title}>
      <Handle type="target" position={Position.Top} isConnectable={false} />
      {flagged && (
        <span className="pg-node-pending" aria-label={`${pending} recommended link${pending === 1 ? "" : "s"} to review`}>
          {pending}
        </span>
      )}
      <span className="pg-node-position">{step.position}</span>
      <span className="pg-node-title">{title}</span>
      {step.kind === "image" && <span className="pg-node-badge">Figure</span>}
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
      {/* Where a link that skips a tier leaves and arrives: the right side,
          next to the lanes. */}
      <Handle id="lane-out" type="source" position={Position.Right} isConnectable={false} className="pg-lane-handle" />
      <Handle id="lane-in" type="target" position={Position.Right} isConnectable={false} className="pg-lane-handle" />
    </div>
  );
}

function LabelNode({ data }) {
  return <div className="pg-label">{data.text}</div>;
}

// A row of the path: everything in it can be taught once the rows above it are.
function TierNode({ data }) {
  return (
    <div className={`pg-tier ${data.number % 2 === 0 ? "is-alt" : ""}`.trim()} style={{ width: data.width, height: data.height }}>
      <span className="pg-tier-name">
        Tier {data.number}
        {data.number === 1 && <small>Start here</small>}
      </span>
    </div>
  );
}

const NODE_TYPES = { concept: ConceptNode, label: LabelNode, tier: TierNode, spacer: () => <div className="pg-spacer" /> };

// A link that skips a tier: out to its lane, down the lane, and back in, so
// it never crosses the tiers in between.
function LaneEdge({ id, sourceX, sourceY, targetX, targetY, data, markerEnd, style }) {
  const path = `M ${sourceX} ${sourceY} H ${data.laneX} V ${targetY} H ${targetX}`;
  return <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} />;
}

const EDGE_TYPES = { lane: LaneEdge };

function Canvas({ steps, selectedId, onSelect, editable, showPending = false, onDrop }) {
  const layout = useMemo(() => {
    const graph = buildGraph(steps, selectedId);
    // Pending recommendations are the teacher's to act on; the read-only page
    // has no way to act, so it shows no flags.
    return {
      ...graph,
      nodes: graph.nodes.map((node) => ({ ...node, data: { ...node.data, showPending } })),
    };
  }, [steps, selectedId, showPending]);
  const [nodes, setNodes, onNodesChange] = useNodesState(layout.nodes);
  const { getIntersectingNodes } = useReactFlow();

  useEffect(() => {
    setNodes(layout.nodes);
  }, [layout, setNodes]);

  function handleDragStop(_event, node) {
    const target = getIntersectingNodes(node).find(
      (other) => other.type === "concept" && other.id !== node.id,
    );
    // Positions are never kept: the box returns to its place in the layout.
    setNodes(layout.nodes);
    if (target) onDrop(Number(node.id), Number(target.id));
  }

  return (
    <ReactFlow
      nodes={nodes}
      edges={layout.edges}
      nodeTypes={NODE_TYPES}
      edgeTypes={EDGE_TYPES}
      onNodesChange={onNodesChange}
      onNodeClick={(_event, node) => node.type === "concept" && onSelect(Number(node.id))}
      onPaneClick={() => onSelect(null)}
      onNodeDragStop={editable ? handleDragStop : undefined}
      nodesDraggable={editable}
      nodesConnectable={false}
      fitView
      minZoom={0.2}
    >
      <Background gap={24} />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}

export default function PathGraph(props) {
  return (
    <div className="pg-canvas">
      <ReactFlowProvider>
        <Canvas {...props} />
      </ReactFlowProvider>
    </div>
  );
}
