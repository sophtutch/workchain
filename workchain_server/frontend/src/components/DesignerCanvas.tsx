import { useCallback, useMemo, useRef, useState, type DragEvent } from "react";
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  addEdge,
  type Connection,
  type Edge,
  type OnEdgesChange,
  type OnNodesChange,
  type ReactFlowInstance,
  type Node,
} from "reactflow";
import { StepNode } from "./StepNode";
import { AnchorNode } from "./AnchorNode";
import { BlockNode } from "./BlockNode";
import {
  isBlockAnchor,
  START_NODE_ID,
  END_NODE_ID,
  type DesignerNode,
} from "../lib/graphToDraft";
import type { HandlerDescriptor } from "../api/types";


interface DesignerCanvasProps {
  nodes: DesignerNode[];
  edges: Edge[];
  handlers: HandlerDescriptor[];
  selectedId: string | null;
  onNodesChange: OnNodesChange;
  onEdgesChange: OnEdgesChange;
  onAddEdge: (edge: Edge) => void;
  onUpdateEdge: (oldEdge: Edge, newConnection: Connection) => void;
  onDropHandler: (handlerName: string, position: { x: number; y: number }) => void;
  onSelect: (id: string | null) => void;
  onNodeDragStop: (nodeId: string, position: { x: number; y: number }) => void;
  setReactFlowInstance: (instance: ReactFlowInstance) => void;
}

const nodeTypes = {
  step: StepNode,
  anchor: AnchorNode,
  block: BlockNode,
};

/**
 * React Flow canvas with horizontal (LR) layout.
 * Edges between outside nodes and block-internal nodes are rejected.
 */
export function DesignerCanvas({
  nodes,
  edges,
  handlers,
  selectedId,
  onNodesChange,
  onEdgesChange,
  onAddEdge,
  onUpdateEdge,
  onDropHandler,
  onSelect,
  onNodeDragStop,
  setReactFlowInstance,
}: DesignerCanvasProps) {
  const [minimapVisible, setMinimapVisible] = useState(true);

  const handlersByName = useMemo(() => {
    const m = new Map<string, HandlerDescriptor>();
    for (const h of handlers) m.set(h.name, h);
    return m;
  }, [handlers]);

  const handleNodesChange: OnNodesChange = useCallback(
    (changes) => {
      const filtered = changes.filter((c) => {
        if (c.type === "remove") {
          return c.id !== START_NODE_ID && c.id !== END_NODE_ID
            && !isBlockAnchor(c.id);
        }
        return true;
      });
      onNodesChange(filtered);
    },
    [onNodesChange],
  );
  const handleEdgesChange: OnEdgesChange = useCallback(
    (changes) => onEdgesChange(changes),
    [onEdgesChange],
  );

  // Use refs for node lookups to avoid recreating callbacks on every node change.
  const nodesRef = useRef(nodes);
  nodesRef.current = nodes;
  const edgesRef = useRef(edges);
  edgesRef.current = edges;

  const onConnect = useCallback(
    (params: Connection) => {
      if (!params.source || !params.target || params.source === params.target) return;

      const curNodes = nodesRef.current;
      const getParent = (id: string) => curNodes.find((n) => n.id === id)?.parentNode;
      const srcParent = getParent(params.source);
      const tgtParent = getParent(params.target);
      const srcIsBlock = curNodes.some((n) => n.id === params.source && n.type === "block");
      const tgtIsBlock = curNodes.some((n) => n.id === params.target && n.type === "block");

      if (!srcIsBlock && !tgtIsBlock) {
        if (srcParent !== tgtParent) return;
      }

      const newEdge: Edge = {
        id: `${params.source}:${params.sourceHandle ?? "result"}->${params.target}:${params.targetHandle ?? "deps"}`,
        source: params.source,
        target: params.target,
        sourceHandle: params.sourceHandle ?? "result",
        targetHandle: params.targetHandle ?? "deps",
      };
      const merged = addEdge(newEdge, edgesRef.current);
      const added = merged.find((e) => e.id === newEdge.id);
      if (added) onAddEdge(added);
    },
    [onAddEdge],
  );

  const onEdgeUpdate = useCallback(
    (oldEdge: Edge, newConnection: Connection) => {
      if (!newConnection.source || !newConnection.target) return;
      if (newConnection.source === newConnection.target) return;

      const curNodes = nodesRef.current;
      const getParent = (id: string) => curNodes.find((n) => n.id === id)?.parentNode;
      const srcParent = getParent(newConnection.source);
      const tgtParent = getParent(newConnection.target);
      const srcIsBlock = curNodes.some((n) => n.id === newConnection.source && n.type === "block");
      const tgtIsBlock = curNodes.some((n) => n.id === newConnection.target && n.type === "block");
      if (!srcIsBlock && !tgtIsBlock && srcParent !== tgtParent) return;

      onUpdateEdge(oldEdge, newConnection);
    },
    [onUpdateEdge],
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

  const nodesWithSelection: DesignerNode[] = useMemo(
    () => nodes.map((n) => {
      const shouldBeSelected = n.id === selectedId;
      return n.selected === shouldBeSelected ? n : { ...n, selected: shouldBeSelected };
    }),
    [nodes, selectedId],
  );

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      if (node.type === "step" || node.type === "block") {
        onSelect(node.id);
      }
    },
    [onSelect],
  );

  const handleNodeDragStop = useCallback(
    (_: React.MouseEvent, node: Node) => {
      onNodeDragStop(node.id, node.position);
    },
    [onNodeDragStop],
  );

  return (
    <div
      className={`canvas${minimapVisible ? "" : " canvas--map-hidden"}`}
      onDrop={onDrop}
      onDragOver={onDragOver}
    >
      <ReactFlow
        nodes={nodesWithSelection}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={handleNodesChange}
        onEdgesChange={handleEdgesChange}
        onConnect={onConnect}
        onEdgeUpdate={onEdgeUpdate}
        edgesUpdatable
        onInit={setReactFlowInstance}
        onNodeClick={handleNodeClick}
        onPaneClick={() => onSelect(null)}
        onNodeDragStop={handleNodeDragStop}
        fitView
        fitViewOptions={{ padding: 0.1 }}
        defaultEdgeOptions={{ type: "bezier" }}
      >
        <Background
          gap={20}
          color="#1e293b"
          variant={BackgroundVariant.Lines}
          lineWidth={0.5}
        />
        <Controls position="bottom-right" />
        {minimapVisible && (
          <MiniMap
            position="bottom-right"
            pannable
            zoomable
            nodeColor="#1f2937"
            maskColor="rgba(0, 0, 0, 0.7)"
          />
        )}
      </ReactFlow>
      <button
        className={`canvas__map-toggle${minimapVisible ? "" : " canvas__map-toggle--collapsed"}`}
        onClick={() => setMinimapVisible((v) => !v)}
        title={minimapVisible ? "Hide mini-map" : "Show mini-map"}
      >
        {minimapVisible ? "Hide Map" : "Show Map"}
      </button>
    </div>
  );
}

