import { useCallback, useMemo, useState } from "react";
import Form from "@rjsf/core";
import validator from "@rjsf/validator-ajv8";
import type { IChangeEvent } from "@rjsf/core";
import type { RJSFSchema } from "@rjsf/utils";
import { Rocket, X, ChevronRight, Settings, Lock } from "lucide-react";
import type { HandlerDescriptor, WorkflowTemplate } from "../api/types";
import { launchTemplate } from "../api/client";

interface TemplateLaunchModalProps {
  template: WorkflowTemplate;
  handlers: HandlerDescriptor[];
  onClose: () => void;
  onLaunched: (workflowName: string, workflowId: string) => void;
}

interface ConfigurableStep {
  name: string;
  handler: string;
  shortHandler: string;
  schema: RJSFSchema;
  defaultConfig: Record<string, unknown>;
}

interface ReadonlyStep {
  name: string;
  shortHandler: string;
}

/**
 * Modal overlay for configuring and launching a workflow from a template.
 *
 * Shows a workflow name input, expandable accordion for steps with
 * configurable fields (driven by RJSF from handler config_schema),
 * and a collapsed list for steps that use defaults only.
 */
export function TemplateLaunchModal({
  template,
  handlers,
  onClose,
  onLaunched,
}: TemplateLaunchModalProps) {
  const [nameOverride, setNameOverride] = useState(template.name);
  const [configOverrides, setConfigOverrides] = useState<
    Record<string, Record<string, unknown>>
  >(() => {
    const init: Record<string, Record<string, unknown>> = {};
    for (const step of template.steps) {
      if (step.config && Object.keys(step.config).length > 0) {
        init[step.name] = { ...step.config };
      }
    }
    return init;
  });
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set());
  const [launching, setLaunching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Build handler lookup keyed by dotted path (h.name matches step.handler).
  const handlerMap = useMemo(() => {
    const m = new Map<string, HandlerDescriptor>();
    for (const h of handlers) m.set(h.name, h);
    return m;
  }, [handlers]);

  // Partition steps into configurable (has schema properties) and readonly.
  const { configurable, readonly } = useMemo(() => {
    const configurable: ConfigurableStep[] = [];
    const readonly: ReadonlyStep[] = [];

    for (const step of template.steps) {
      const handler = handlerMap.get(step.handler);
      const schema = handler?.config_schema as RJSFSchema | undefined;
      const props = schema?.properties;
      const hasProps =
        props && typeof props === "object" && Object.keys(props).length > 0;
      const short = step.handler.split(".").pop() ?? step.handler;

      if (hasProps && schema) {
        configurable.push({
          name: step.name,
          handler: step.handler,
          shortHandler: short,
          schema,
          defaultConfig: (step.config ?? {}) as Record<string, unknown>,
        });
      } else {
        readonly.push({ name: step.name, shortHandler: short });
      }
    }
    return { configurable, readonly };
  }, [template.steps, handlerMap]);

  const toggleStep = useCallback((name: string) => {
    setExpandedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }, []);

  const updateStepConfig = useCallback(
    (stepName: string, data: Record<string, unknown>) => {
      setConfigOverrides((prev) => ({ ...prev, [stepName]: data }));
    },
    [],
  );

  const handleLaunch = useCallback(async () => {
    setLaunching(true);
    setError(null);
    try {
      // Only send overrides for steps whose config differs from template defaults.
      const overrides: Record<string, Record<string, unknown>> = {};
      for (const [stepName, config] of Object.entries(configOverrides)) {
        const templateStep = template.steps.find((s) => s.name === stepName);
        const defaultConfig = templateStep?.config ?? {};
        if (JSON.stringify(config) !== JSON.stringify(defaultConfig)) {
          overrides[stepName] = config;
        }
      }

      const hasOverrides = Object.keys(overrides).length > 0;
      const result = await launchTemplate(
        template.id,
        nameOverride !== template.name ? nameOverride : undefined,
        hasOverrides ? overrides : undefined,
      );
      onLaunched(result.name, result.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Launch failed");
    } finally {
      setLaunching(false);
    }
  }, [template, nameOverride, configOverrides, onLaunched]);

  return (
    <div className="launch-modal-overlay" onClick={onClose}>
      <div
        className="launch-modal"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}
        role="dialog"
        aria-modal="true"
        aria-label={`Launch ${template.name}`}
      >
        {/* Header */}
        <div className="launch-modal__header">
          <div>
            <h2 className="launch-modal__title">{template.name}</h2>
            {template.description && (
              <p className="launch-modal__desc">{template.description}</p>
            )}
            <span className="launch-modal__meta">
              {template.steps.length} steps
            </span>
          </div>
          <button
            type="button"
            className="launch-modal__close"
            onClick={onClose}
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="launch-modal__body">
          {/* Workflow name */}
          <div className="launch-modal__field">
            <label className="launch-modal__label" htmlFor="launch-wf-name">
              Workflow Name
            </label>
            <input
              id="launch-wf-name"
              className="launch-modal__input"
              value={nameOverride}
              onChange={(e) => setNameOverride(e.target.value)}
            />
          </div>

          {/* Configurable steps */}
          {configurable.length > 0 && (
            <div className="launch-modal__section">
              <h3 className="launch-modal__section-title"><Settings size={14} /> Configuration</h3>
              {configurable.map((step) => {
                const isOpen = expandedSteps.has(step.name);
                return (
                  <div key={step.name} className="launch-modal__step">
                    <button
                      type="button"
                      className="launch-modal__step-header"
                      onClick={() => toggleStep(step.name)}
                    >
                      <span
                        className={`launch-modal__chevron${isOpen ? " launch-modal__chevron--open" : ""}`}
                      >
                        <ChevronRight size={14} />
                      </span>
                      <span className="launch-modal__step-name">
                        {step.name}
                      </span>
                      <span className="launch-modal__step-handler">
                        {step.shortHandler}
                      </span>
                    </button>
                    {isOpen && (
                      <div className="launch-modal__step-body">
                        <Form
                          schema={step.schema}
                          validator={validator}
                          formData={
                            configOverrides[step.name] ?? step.defaultConfig
                          }
                          onChange={(e: IChangeEvent) =>
                            updateStepConfig(
                              step.name,
                              (e.formData ?? {}) as Record<string, unknown>,
                            )
                          }
                          liveValidate
                        >
                          <div />
                        </Form>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {/* Read-only steps */}
          {readonly.length > 0 && (
            <div className="launch-modal__section">
              <h3 className="launch-modal__section-title"><Lock size={14} /> Other Steps</h3>
              {readonly.map((step) => (
                <div
                  key={step.name}
                  className="launch-modal__step launch-modal__step--readonly"
                >
                  <div className="launch-modal__step-header launch-modal__step-header--static">
                    <span className="launch-modal__step-name">
                      {step.name}
                    </span>
                    <span className="launch-modal__step-handler">
                      {step.shortHandler}
                    </span>
                    <span className="launch-modal__step-tag">
                      Uses defaults
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="launch-modal__footer">
          {error && <div className="launch-modal__error">{error}</div>}
          <button
            type="button"
            className="btn btn--ghost"
            onClick={onClose}
            disabled={launching}
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn btn--primary"
            onClick={handleLaunch}
            disabled={launching || !nameOverride.trim()}
          >
            <Rocket size={14} /> {launching ? "Launching…" : "Launch Workflow"}
          </button>
        </div>
      </div>
    </div>
  );
}
