import type { DragEvent } from "react";
import type { HandlerDescriptor } from "../api/types";

interface HandlerPaletteProps {
  handlers: HandlerDescriptor[];
  loading: boolean;
  error: string | null;
}

/**
 * Left sidebar: draggable list of registered handlers. Non-launchable
 * handlers are greyed out — they still appear so users understand why
 * a handler from their codebase isn't usable.
 */
export function HandlerPalette({ handlers, loading, error }: HandlerPaletteProps) {
  const onDragStart = (event: DragEvent<HTMLElement>, handlerName: string) => {
    event.dataTransfer.setData("application/workchain-handler", handlerName);
    event.dataTransfer.effectAllowed = "move";
  };

  return (
    <aside className="palette">
      <h2 className="palette__title">Handlers</h2>
      {loading && <div className="palette__status">Loading…</div>}
      {error && <div className="palette__error">{error}</div>}
      {!loading && !error && handlers.length === 0 && (
        <div className="palette__status">
          No handlers registered. Set <code>WORKCHAIN_PLUGINS</code> and restart
          the server.
        </div>
      )}
      <ul className="palette__list">
        {handlers.map((h) => {
          const shortName = h.qualname;
          const disabled = !h.launchable;
          return (
            <li
              key={h.name}
              className={`palette__item${disabled ? " palette__item--disabled" : ""}`}
              draggable={!disabled}
              onDragStart={(e) => !disabled && onDragStart(e, h.name)}
              title={
                disabled
                  ? "Handler is not launchable: needs a StepConfig subclass and a StepResult subclass in its signature."
                  : h.doc ?? h.name
              }
            >
              <div className="palette__item-name">{shortName}</div>
              <div className="palette__item-module">{h.module}</div>
              <div className="palette__item-badges">
                {h.is_async && <span className="badge badge--async">async</span>}
                {h.idempotent && <span className="badge badge--idem">idempotent</span>}
                {disabled && <span className="badge badge--danger">no schema</span>}
              </div>
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
