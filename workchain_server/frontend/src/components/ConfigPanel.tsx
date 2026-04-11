import { useMemo } from "react";
import Form from "@rjsf/core";
import validator from "@rjsf/validator-ajv8";
import type { IChangeEvent } from "@rjsf/core";
import type { RJSFSchema } from "@rjsf/utils";
import type { HandlerDescriptor } from "../api/types";
import { isStepNode, type DesignerNode, type StepNode } from "../lib/graphToDraft";

interface ConfigPanelProps {
  selectedNode: DesignerNode | null;
  handler: HandlerDescriptor | null;
  onStepNameChange: (nodeId: string, name: string) => void;
  onConfigChange: (nodeId: string, values: Record<string, unknown>) => void;
  onBlockLabelChange: (nodeId: string, label: string) => void;
  onDelete: (nodeId: string) => void;
  onUnparent: (nodeId: string) => void;
  errors: string[];
}

/**
 * Right sidebar.  Shows context-appropriate controls for the selected node:
 * - Step node: editable step name, handler info, RJSF config form
 * - Block node: editable label, delete
 * - Nothing selected: placeholder message
 */
export function ConfigPanel({
  selectedNode,
  handler,
  onStepNameChange,
  onConfigChange,
  onBlockLabelChange,
  onDelete,
  onUnparent,
  errors,
}: ConfigPanelProps) {
  if (!selectedNode) {
    return (
      <aside className="config-panel">
        <div className="config-panel__empty">
          Select a step on the canvas to edit its config.
        </div>
      </aside>
    );
  }

  if (selectedNode.type === "block") {
    return (
      <aside className="config-panel">
        <div className="config-panel__header">
          <h2 className="config-panel__title">Block config</h2>
          <button
            type="button"
            className="btn btn--danger btn--sm"
            onClick={() => onDelete(selectedNode.id)}
          >
            Delete
          </button>
        </div>
        <div className="config-panel__field">
          <label className="config-panel__label" htmlFor="block-label">
            Block name
          </label>
          <input
            id="block-label"
            className="config-panel__input"
            value={(selectedNode.data as { label: string }).label}
            onChange={(e) => onBlockLabelChange(selectedNode.id, e.target.value)}
          />
        </div>
        <div className="config-panel__doc">
          A block groups steps into a contained sub-workflow.
          External edges connect to the block handles, not to internal steps.
          All internal steps must complete before downstream dependents proceed.
        </div>
      </aside>
    );
  }

  if (!isStepNode(selectedNode)) {
    return (
      <aside className="config-panel">
        <div className="config-panel__empty">
          This node is not configurable.
        </div>
      </aside>
    );
  }

  return (
    <StepConfigPanel
      node={selectedNode}
      handler={handler}
      onStepNameChange={onStepNameChange}
      onConfigChange={onConfigChange}
      onDelete={onDelete}
      onUnparent={onUnparent}
      errors={errors}
    />
  );
}

// ---------------------------------------------------------------------------
// Step-specific config panel (extracted for clarity)
// ---------------------------------------------------------------------------

function StepConfigPanel({
  node,
  handler,
  onStepNameChange,
  onConfigChange,
  onDelete,
  onUnparent,
  errors,
}: {
  node: StepNode;
  handler: HandlerDescriptor | null;
  onStepNameChange: (id: string, name: string) => void;
  onConfigChange: (id: string, values: Record<string, unknown>) => void;
  onDelete: (id: string) => void;
  onUnparent: (id: string) => void;
  errors: string[];
}) {
  const schema = useMemo<RJSFSchema>(() => {
    if (!handler?.config_schema) return { type: "object", properties: {} };
    return handler.config_schema as RJSFSchema;
  }, [handler]);

  return (
    <aside className="config-panel">
      <div className="config-panel__header">
        <h2 className="config-panel__title">Step config</h2>
        <div className="config-panel__actions">
          {node.parentNode && (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => onUnparent(node.id)}
              title="Remove from block"
            >
              Ungroup
            </button>
          )}
          <button
            type="button"
            className="btn btn--danger btn--sm"
            onClick={() => onDelete(node.id)}
          >
            Delete
          </button>
        </div>
      </div>
      <div className="config-panel__field">
        <label className="config-panel__label" htmlFor="step-name">
          Step name
        </label>
        <input
          id="step-name"
          className="config-panel__input"
          value={node.data.stepName}
          onChange={(e) => onStepNameChange(node.id, e.target.value)}
        />
      </div>
      <div className="config-panel__handler">
        Handler: <code>{node.data.handlerName}</code>
      </div>
      {node.parentNode && (
        <div className="config-panel__handler">
          Block: <code>{node.parentNode}</code>
        </div>
      )}
      {handler?.doc && (
        <div className="config-panel__doc">{handler.doc}</div>
      )}
      {errors.length > 0 && (
        <ul className="config-panel__errors">
          {errors.map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
      )}
      <Form
        schema={schema}
        validator={validator}
        formData={node.data.configValues}
        onChange={(e: IChangeEvent) =>
          onConfigChange(node.id, (e.formData ?? {}) as Record<string, unknown>)
        }
        liveValidate
      >
        <div />
      </Form>
    </aside>
  );
}
