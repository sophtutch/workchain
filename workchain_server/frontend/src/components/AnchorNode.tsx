import { Handle, Position, type NodeProps } from "reactflow";
import type { AnchorNodeData } from "../lib/graphToDraft";
import { START_NODE_ID } from "../lib/graphToDraft";

/**
 * Anchor node for workflow START/END and block-internal S/E sync points.
 */
export function AnchorNode({ id, data }: NodeProps<AnchorNodeData>) {
  const isSource = id === START_NODE_ID || data.label === "S";
  const isSmall = data.label === "S" || data.label === "E";

  return (
    <div className={`anchor-node${isSmall ? " anchor-node--small" : ""}`}>
      {!isSource && (
        <Handle type="target" id="deps" position={Position.Left}
          className="step-handle step-handle--input" />
      )}
      <div className="anchor-node__label">{data.label}</div>
      {isSource && (
        <Handle type="source" id="result" position={Position.Right}
          className="step-handle step-handle--output" />
      )}
    </div>
  );
}
