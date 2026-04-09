import { useCallback, useMemo, type DragEvent } from "react";
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  addEdge,
  type Connection,
  type Edge,
  type OnEdgesChange,
  type OnNodesChange,
  type ReactFlowInstance,
  applyNodeChanges,
  applyEdgeChanges,
} from "reactflow";
import { StepNode } from "./StepNode";
import type { StepNode as StepNodeType, StepNodeData } from "../lib/graphToDraft";
import type { HandlerDescriptor } from "../api/types";

interface DesignerCanvasProps {
  nodes: StepNodeType[];
  edges: Edge[];
  handlers: HandlerDescriptor[];
  selectedId: string | null;
  onNodesChange: OnNodesChange;
  onEdgesChange: OnEdgesChange;
  onAddEdge: (edge: Edge) => void;
  onDropHandler: (handlerName: string, position: { x: number; y: number }) => void;
  onSelect: (id: string | null) => void;
  setReactFlowInstance: (instance: ReactFlowInstance) => void;
}

const nodeTypes = { step: StepNode };

/**
 * React Flow canvas. Accepts drops from HandlerPalette, adds edges on
 * connect, and reports selection changes upward so ConfigPanel can render
 * a form for the selected node.
 */
export function DesignerCanvas({
  nodes,
  edges,
  handlers,
  selectedId,
  onNodesChange,
  onEdgesChange,
  onAddEdge,
  onDropHandler,
  onSelect,
  setReactFlowInstance,
}: DesignerCanvasProps) {
  const handlersByName = useMemo(() => {
    const m = new Map<string, HandlerDescriptor>();
    for (const h of handlers) m.set(h.name, h);
    return m;
  }, [handlers]);

  const handleNodesChange: OnNodesChange = useCallback(
    (changes) => onNodesChange(changes),
    [onNodesChange],
  );
  const handleEdgesChange: OnEdgesChange = useCallback(
    (changes) => onEdgesChange(changes),
    [onEdgesChange],
  );

  const onConnect = useCallback(
    (params: Connection) => {
      if (!params.source || !params.target || params.source === params.target) return;
      const newEdge: Edge = {
        id: `${params.source}->${params.target}`,
        source: params.source,
        target: params.target,
      };
      const merged = addEdge(newEdge, edges);
      // React Flow's addEdge returns the full edge list with the new edge
      // appended; dispatch the single new edge through the reducer.
      const added = merged.find((e) => e.id === newEdge.id);
      if (added) onAddEdge(added);
    },
    [edges, onAddEdge],
  );

  const onDragOver = useCallback((event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }, []);

  const onDrop = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      const handlerName = event.dataTransfer.getData("application/workchain-handler");
      if (!handlerName) return;
      if (!handlersByName.has(handlerName)) return;
      const bounds = event.currentTarget.getBoundingClientRect();
      onDropHandler(handlerName, {
        x: event.clientX - bounds.left,
        y: event.clientY - bounds.top,
      });
    },
    [handlersByName, onDropHandler],
  );

  const nodesWithSelection: StepNodeType[] = useMemo(
    () => nodes.map((n) => ({ ...n, selected: n.id === selectedId })),
    [nodes, selectedId],
  );

  return (
    <div className="canvas" onDrop={onDrop} onDragOver={onDragOver}>
      <ReactFlow
        nodes={nodesWithSelection}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={handleNodesChange}
        onEdgesChange={handleEdgesChange}
        onConnect={onConnect}
        onInit={setReactFlowInstance}
        onNodeClick={(_, node) => onSelect(node.id)}
        onPaneClick={() => onSelect(null)}
        fitView
        defaultEdgeOptions={{ type: "smoothstep" }}
      >
        <Background gap={16} />
        <Controls />
        <MiniMap pannable zoomable />
      </ReactFlow>
    </div>
  );
}

// Re-export React Flow helpers so App.tsx has a single import site.
export { applyNodeChanges, applyEdgeChanges };
export type { StepNodeData, Edge, StepNodeType };
