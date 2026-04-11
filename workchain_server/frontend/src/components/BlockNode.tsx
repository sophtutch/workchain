import { Handle, NodeResizer, Position, type NodeProps } from "reactflow";

export interface BlockNodeData {
  label: string;
}

/**
 * Sub-workflow block — a resizable container that groups steps.
 */
export function BlockNode({ data, selected }: NodeProps<BlockNodeData>) {
  return (
    <div className={`block-node${selected ? " block-node--selected" : ""}`}>
      <NodeResizer
        minWidth={200}
        minHeight={120}
        isVisible={selected ?? false}
        lineClassName="block-node__resize-line"
        handleClassName="block-node__resize-handle"
      />
      <Handle type="target" id="deps" position={Position.Left}
        className="step-handle step-handle--input" title="Input" />
      <div className="block-node__header">
        <span className="block-node__label">{data.label}</span>
      </div>
      <Handle type="source" id="result" position={Position.Right}
        className="step-handle step-handle--output" title="Output" />
    </div>
  );
}
