import { Handle, Position, type NodeProps } from "reactflow";
import type { StepNodeData } from "../lib/graphToDraft";

/**
 * Custom React Flow node that renders a single step. Top handle = incoming
 * dependency edges (this step depends on source); bottom handle = outgoing
 * edges (dependents of this step).
 */
export function StepNode({ data, selected }: NodeProps<StepNodeData>) {
  const shortName = data.handlerName.split(".").pop() ?? data.handlerName;
  return (
    <div className={`step-node${selected ? " step-node--selected" : ""}`}>
      <Handle type="target" position={Position.Top} />
      <div className="step-node__name">{data.stepName}</div>
      <div className="step-node__handler" title={data.handlerName}>
        {shortName}
      </div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}
