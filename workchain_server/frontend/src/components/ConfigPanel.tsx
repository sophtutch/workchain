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
  onConfigChange: (nodeId: string, values: Record<string, unknown>) => void;
  onBlockLabelChange: (nodeId: string, label: string) => void;
  onDelete: (nodeId: string) => void;
  onUnparent: (nodeId: string) => void;
}

export function ConfigPanel({
  selectedNode,
  handler,
  onConfigChange,
  onBlockLabelChange,
  onDelete,
  onUnparent,
}: ConfigPanelProps) {
  if (!selectedNode) {
    return (
      <aside className="config-panel">
        <div className="config-panel__empty">
          Select a step to configure.
        </div>
      </aside>
    );
  }

  if (selectedNode.type === "block") {
    return (
      <aside className="config-panel">
        <div className="config-panel__header">
          <h2 className="config-panel__title">Block</h2>
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
            Name
          </label>
          <input
            id="block-label"
            className="config-panel__input"
            value={(selectedNode.data as { label: string }).label}
            onChange={(e) => onBlockLabelChange(selectedNode.id, e.target.value)}
          />
        </div>
      </aside>
    );
  }

  if (!isStepNode(selectedNode)) {
    return (
      <aside className="config-panel">
        <div className="config-panel__empty">
          Not configurable.
        </div>
      </aside>
    );
  }

  return (
    <StepConfigPanel
      node={selectedNode}
      handler={handler}
      onConfigChange={onConfigChange}
      onDelete={onDelete}
      onUnparent={onUnparent}
    />
  );
}

function StepConfigPanel({
  node,
  handler,
  onConfigChange,
  onDelete,
  onUnparent,
}: {
  node: StepNode;
  handler: HandlerDescriptor | null;
  onConfigChange: (id: string, values: Record<string, unknown>) => void;
  onDelete: (id: string) => void;
  onUnparent: (id: string) => void;
}) {
  const schema = useMemo<RJSFSchema>(() => {
    if (!handler?.config_schema) return { type: "object", properties: {} };
    return handler.config_schema as RJSFSchema;
  }, [handler]);

  const shortHandler = node.data.handlerName.split(".").pop() ?? node.data.handlerName;

  return (
    <aside className="config-panel">
      <div className="config-panel__header">
        <code className="config-panel__step-name">{node.data.stepName}</code>
        <span className="config-panel__handler-short" title={node.data.handlerName}>
          {shortHandler}
        </span>
        <div className="config-panel__actions">
          {node.parentNode && (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => onUnparent(node.id)}
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
      <Form
        schema={schema}
        validator={validator}
        formData={node.data.configValues}
        onChange={(e: IChangeEvent) =>
          onConfigChange(node.id, (e.formData ?? {}) as Record<string, unknown>)
        }
        liveValidate
        showErrorList={false}
      >
        <div />
      </Form>
    </aside>
  );
}
