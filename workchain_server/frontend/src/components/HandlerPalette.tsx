import { useMemo, useState, type DragEvent } from "react";
import { Zap, Play, X } from "lucide-react";
import type { HandlerDescriptor } from "../api/types";

interface HandlerPaletteProps {
  handlers: HandlerDescriptor[];
  loading: boolean;
  error: string | null;
}

const UNCATEGORISED = "Uncategorised";

/**
 * Left sidebar: n8n-inspired palette of registered handlers grouped by
 * category with search, descriptions and visual badges.  Non-launchable
 * handlers are greyed out — they still appear so users understand why
 * a handler from their codebase isn't usable.
 */
export function HandlerPalette({ handlers, loading, error }: HandlerPaletteProps) {
  const [search, setSearch] = useState("");
  // Categories start collapsed — the set tracks which are OPEN.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const onDragStart = (event: DragEvent<HTMLElement>, handlerName: string) => {
    event.dataTransfer.setData("application/workchain-handler", handlerName);
    event.dataTransfer.effectAllowed = "move";
  };

  // Filter by search term (name, module, description, category).
  const filtered = useMemo(() => {
    if (!search.trim()) return handlers;
    const q = search.toLowerCase();
    return handlers.filter(
      (h) =>
        h.qualname.toLowerCase().includes(q) ||
        h.module.toLowerCase().includes(q) ||
        (h.description ?? "").toLowerCase().includes(q) ||
        (h.category ?? "").toLowerCase().includes(q),
    );
  }, [handlers, search]);

  // Group by category, preserving original order within each group.
  const grouped = useMemo(() => {
    const m = new Map<string, HandlerDescriptor[]>();
    for (const h of filtered) {
      const cat = h.category ?? UNCATEGORISED;
      const list = m.get(cat) ?? [];
      list.push(h);
      m.set(cat, list);
    }
    // Sort categories: named categories alphabetically, Uncategorised last.
    return [...m.entries()].sort(([a], [b]) => {
      if (a === UNCATEGORISED) return 1;
      if (b === UNCATEGORISED) return -1;
      return a.localeCompare(b);
    });
  }, [filtered]);

  const toggleCategory = (cat: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(cat)) next.delete(cat);
      else next.add(cat);
      return next;
    });
  };

  return (
    <aside className="palette">
      <h2 className="palette__title">Steps</h2>

      {/* Search */}
      <div className="palette__search-wrap">
        <input
          className="palette__search"
          type="text"
          placeholder="Search steps…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        {search && (
          <button
            type="button"
            className="palette__search-clear"
            onClick={() => setSearch("")}
            aria-label="Clear search"
          >
            <X size={14} />
          </button>
        )}
      </div>

      {loading && <div className="palette__status">Loading…</div>}
      {error && <div className="palette__error">{error}</div>}
      {!loading && !error && handlers.length === 0 && (
        <div className="palette__status">
          No handlers registered. Set <code>WORKCHAIN_PLUGINS</code> and restart
          the server.
        </div>
      )}
      {!loading && !error && filtered.length === 0 && handlers.length > 0 && (
        <div className="palette__status">No matching steps.</div>
      )}

      {/* Category groups */}
      <div className="palette__groups">
        {grouped.map(([cat, items]) => {
          const isOpen = expanded.has(cat);
          return (
            <div key={cat} className="palette__group">
              <button
                type="button"
                className="palette__group-header"
                onClick={() => toggleCategory(cat)}
              >
                <span className={`palette__chevron${isOpen ? " palette__chevron--open" : ""}`}>
                  ›
                </span>
                <span className="palette__group-name">{cat}</span>
                <span className="palette__group-count">{items.length}</span>
              </button>
              {isOpen && (
                <ul className="palette__list">
                  {items.map((h) => (
                    <HandlerCard
                      key={h.name}
                      handler={h}
                      onDragStart={onDragStart}
                    />
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </div>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Individual handler card
// ---------------------------------------------------------------------------

function HandlerCard({
  handler: h,
  onDragStart,
}: {
  handler: HandlerDescriptor;
  onDragStart: (event: DragEvent<HTMLElement>, name: string) => void;
}) {
  const disabled = !h.launchable;
  const shortName = h.qualname.split(".").pop() ?? h.qualname;

  // Count config fields from JSON schema to show complexity hint.
  const fieldCount = h.config_schema
    ? Object.keys((h.config_schema as Record<string, unknown>)?.properties ?? {}).length
    : 0;

  return (
    <li
      className={`palette__card${disabled ? " palette__card--disabled" : ""}`}
      draggable={!disabled}
      onDragStart={(e) => !disabled && onDragStart(e, h.name)}
      title={
        disabled
          ? "Not launchable: needs typed StepConfig and StepResult subclasses."
          : h.doc ?? h.name
      }
    >
      {/* Icon area — colour indicates step type */}
      <div
        className={`palette__card-icon${h.is_async ? " palette__card-icon--async" : ""}`}
      >
        {h.is_async ? <Zap size={14} /> : <Play size={14} />}
      </div>

      <div className="palette__card-body">
        <div className="palette__card-name">{shortName}</div>
        {h.description && (
          <div className="palette__card-desc">{h.description}</div>
        )}
        <div className="palette__card-meta">
          <span className="palette__card-module">{h.module}</span>
          {fieldCount > 0 && (
            <span className="palette__card-fields">
              {fieldCount} {fieldCount === 1 ? "field" : "fields"}
            </span>
          )}
        </div>
        <div className="palette__card-badges">
          {h.is_async
            ? <span className="badge badge--async">async</span>
            : <span className="badge badge--sync">sync</span>}
          {h.idempotent && <span className="badge badge--idem">idempotent</span>}
          {!h.idempotent && <span className="badge badge--danger">non-idempotent</span>}
          {disabled && <span className="badge badge--danger">no schema</span>}
        </div>
      </div>
    </li>
  );
}
