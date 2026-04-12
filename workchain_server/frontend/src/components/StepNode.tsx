import { useCallback } from "react";
import { Handle, Position, useStore, type NodeProps } from "reactflow";
import { Play, Zap } from "lucide-react";
import type { StepNodeData } from "../lib/graphToDraft";

/**
 * Custom React Flow node for a single workflow step.
 * Primary handles (left/right) always visible.
 * Secondary handles (top/bottom) hidden unless connected to an edge.
 */
export function StepNode({ id, data, selected }: NodeProps<StepNodeData>) {
  const shortName = data.handlerName.split(".").pop() ?? data.handlerName;

  // Subscribe only to edges connected to THIS node, avoiding full-graph
  // re-renders that useEdges() would cause.
  const connectedHandles = useStore(
    useCallback(
      (state) => {
        const set = new Set<string>();
        for (const e of state.edges) {
          if (e.source === id && e.sourceHandle) set.add(e.sourceHandle);
          if (e.target === id && e.targetHandle) set.add(e.targetHandle);
        }
        // Return a stable string so React only re-renders when the set changes.
        return Array.from(set).sort().join(",");
      },
      [id],
    ),
  );

  const isConnected = (handleId: string) => connectedHandles.includes(handleId);

  const hasErrors = data.errors && data.errors.length > 0;

  return (
    <div className={`step-node${selected ? " step-node--selected" : ""}${data.handlerIsAsync ? " step-node--async" : ""}${hasErrors ? " step-node--error" : ""}`}>
      {/* Left: input */}
      <Handle type="target" id="deps" position={Position.Left}
        className="step-handle step-handle--input" />
      {/* Right: output */}
      <Handle type="source" id="result" position={Position.Right}
        className="step-handle step-handle--output" />
      {/* Top: input (left-weighted), output (right-weighted) */}
      <Handle type="target" id="deps-top" position={Position.Top}
        className="step-handle step-handle--input"
        style={{ left: "25%", transform: "translateX(-50%)" }}
        data-connected={isConnected("deps-top") ? "" : undefined} />
      <Handle type="source" id="result-top" position={Position.Top}
        className="step-handle step-handle--output"
        style={{ left: "75%", transform: "translateX(-50%)" }}
        data-connected={isConnected("result-top") ? "" : undefined} />
      {/* Bottom: input (left-weighted), output (right-weighted) */}
      <Handle type="target" id="deps-bottom" position={Position.Bottom}
        className="step-handle step-handle--input"
        style={{ left: "25%", transform: "translateX(-50%)" }}
        data-connected={isConnected("deps-bottom") ? "" : undefined} />
      <Handle type="source" id="result-bottom" position={Position.Bottom}
        className="step-handle step-handle--output"
        style={{ left: "75%", transform: "translateX(-50%)" }}
        data-connected={isConnected("result-bottom") ? "" : undefined} />

      <div className="step-node__body">
        <span className={`step-node__mode-icon${data.handlerIsAsync ? " step-node__mode-icon--async" : ""}`}
          title={data.handlerIsAsync ? "Async step" : "Sync step"}>
          {data.handlerIsAsync ? <Zap size={14} /> : <Play size={14} />}
        </span>
        <div className="step-node__text">
          <div className="step-node__name">{data.stepName}</div>
          <div className="step-node__handler" title={data.handlerName}>
            {shortName}
          </div>
          {data.handlerDescription && (
            <div className="step-node__desc">{data.handlerDescription}</div>
          )}
        </div>
      </div>
    </div>
  );
}
