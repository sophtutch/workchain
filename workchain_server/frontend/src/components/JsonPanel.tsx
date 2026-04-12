/**
 * Syntax-highlighted JSON display panel, styled to match the dark neon theme.
 */

interface JsonPanelProps {
  data: Record<string, unknown> | null;
  label?: string;
}

function renderValue(val: unknown, indent: number): string {
  const pad = "  ".repeat(indent);
  if (val === null || val === undefined) {
    return '<span class="jp-kw">null</span>';
  }
  if (typeof val === "boolean") {
    return `<span class="jp-kw">${val}</span>`;
  }
  if (typeof val === "number") {
    return `<span class="jp-num">${val}</span>`;
  }
  if (typeof val === "string") {
    const escaped = val
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
    return `<span class="jp-str">"${escaped}"</span>`;
  }
  if (Array.isArray(val)) {
    if (val.length === 0) return "[]";
    const items = val
      .map((v) => `${pad}  ${renderValue(v, indent + 1)}`)
      .join(",\n");
    return `[\n${items}\n${pad}]`;
  }
  if (typeof val === "object") {
    const entries = Object.entries(val as Record<string, unknown>);
    if (entries.length === 0) return "{}";
    const items = entries
      .map(
        ([k, v]) =>
          `${pad}  <span class="jp-key">"${k}"</span>: ${renderValue(v, indent + 1)}`,
      )
      .join(",\n");
    return `{\n${items}\n${pad}}`;
  }
  return String(val);
}

export function JsonPanel({ data, label }: JsonPanelProps) {
  if (!data || Object.keys(data).length === 0) {
    return (
      <div className="json-panel">
        {label && <div className="json-panel__label">{label}</div>}
        <div className="json-panel__empty">No data</div>
      </div>
    );
  }

  const html = renderValue(data, 0);

  return (
    <div className="json-panel">
      {label && <div className="json-panel__label">{label}</div>}
      <pre
        className="json-panel__code"
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </div>
  );
}
