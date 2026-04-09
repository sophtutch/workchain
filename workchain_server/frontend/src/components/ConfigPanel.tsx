import { useMemo } from "react";
import Form from "@rjsf/core";
import validator from "@rjsf/validator-ajv8";
import type { IChangeEvent } from "@rjsf/core";
import type { RJSFSchema } from "@rjsf/utils";
import type { HandlerDescriptor } from "../api/types";
import type { StepNode } from "../lib/graphToDraft";

interface ConfigPanelProps {
  node: StepNode | null;
  handler: HandlerDescriptor | null;
  onStepNameChange: (nodeId: string, name: string) => void;
  onConfigChange: (nodeId: string, values: Record<string, unknown>) => void;
  onDelete: (nodeId: string) => void;
  errors: string[];
}

/**
 * Right sidebar. Shows a JSON-schema-driven form (RJSF) for the selected
 * node's config, plus an editable step name. Clearing the selection hides
 * the panel.
 */
export function ConfigPanel({
  node,
  handler,
  onStepNameChange,
  onConfigChange,
  onDelete,
  errors,
}: ConfigPanelProps) {
  const schema = useMemo<RJSFSchema>(() => {
    if (!handler?.config_schema) return { type: "object", properties: {} };
    return handler.config_schema as RJSFSchema;
  }, [handler]);

  if (!node) {
    return (
      <aside className="config-panel">
        <div className="config-panel__empty">
          Select a step on the canvas to edit its config.
        </div>
      </aside>
    );
  }

  return (
    <aside className="config-panel">
      <div className="config-panel__header">
        <h2 className="config-panel__title">Step config</h2>
        <button
          type="button"
          className="btn btn--danger btn--sm"
          onClick={() => onDelete(node.id)}
        >
          Delete
        </button>
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
        {/* Empty children suppresses the default Submit button. */}
        <div />
      </Form>
    </aside>
  );
}
