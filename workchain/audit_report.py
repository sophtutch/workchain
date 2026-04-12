"""Generate self-contained HTML execution reports from audit events.

Usage:
    events = await audit_logger.get_events(workflow_id)
    html = generate_audit_report(events)

    # Pass the workflow for a full graph including unexecuted steps:
    html = generate_audit_report(events, workflow=wf)
"""

from __future__ import annotations

import html
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from workchain.audit import AuditEvent, AuditEventType

if TYPE_CHECKING:
    from datetime import datetime

    from workchain.models import Workflow

# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

CSS = """\
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: #000000; color: #ffffff;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
    font-size: 16px; line-height: 1.6; padding: 2rem 1.5rem;
  }

  /* header */
  .page-header { text-align: center; margin-bottom: 2rem; }
  .page-header h1 { font-size: 2.2rem; font-weight: 700; color: #ffffff; letter-spacing: -0.02em; }
  .page-header .subtitle { font-size: 1.05rem; color: #666666; margin-top: 0.35rem; letter-spacing: 0.02em; }

  /* summary banner */
  .summary-banner {
    background: linear-gradient(135deg, #0a0a0a 0%, #111111 100%);
    border: 1px solid rgba(0,240,255,0.2); border-radius: 10px;
    padding: 1.1rem 1.5rem; margin-bottom: 2rem;
    display: flex; align-items: center; gap: 1.5rem; flex-wrap: wrap;
  }
  .summary-banner .wf-name { font-weight: 700; font-size: 1.2rem; color: #00f0ff; }
  .summary-banner .wf-name code {
    background: rgba(0,240,255,0.1); padding: 0.15em 0.5em; border-radius: 4px;
    font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace; font-size: 0.92em;
  }
  .summary-stats { display: flex; gap: 1rem; flex-wrap: wrap; margin-left: auto; }
  .stat {
    font-size: 0.88rem; font-weight: 600; padding: 0.25em 0.75em;
    border-radius: 999px; text-transform: uppercase; letter-spacing: 0.04em;
  }
  .stat.completed { background: rgba(0,255,136,0.1); color: #00ff88; }
  .stat.failed    { background: rgba(255,51,102,0.1); color: #ff3366; }
  .stat.running   { background: rgba(0,240,255,0.1); color: #00f0ff; }
  .stat.review    { background: rgba(255,170,0,0.1); color: #ffaa00; }
  .stat.neutral   { background: rgba(107,114,128,0.15); color: #6b7280; }

  /* step flow panel */
  .step-flow-panel { background: #0a0a0a; border: 1px solid #1a1a1a; border-radius: 10px; padding: 16px; }

  /* main area */
  .main-area { display: flex; flex-direction: column; }

  /* step section: 3-column grid */
  .step-section {
    display: grid; grid-template-columns: 1fr 260px minmax(360px, 1fr); gap: 20px;
    align-items: stretch; padding: 20px; border-radius: 10px;
    margin-bottom: 12px;
    border: 1px solid #1a1a1a;
  }
  .step-section.sync-step { border-left: 3px solid #bf5fff; }
  .step-section.async-step { border-left: 3px solid #ffaa00; }
  .step-section.discovery { border-left: 3px solid #00ff88; }
  .step-section > .step-flow-panel { height: 100%; box-sizing: border-box; }
  .step-doc { display: flex; flex-direction: column; }
  .step-doc .panel { flex: 1; margin-bottom: 0; display: flex; flex-direction: column; }
  .step-doc .panel .mongo-doc { flex: 1; }

  /* transition column */
  .step-transitions { display: flex; flex-direction: column; gap: 6px; padding: 4px 0; }
  .tx-block {
    border-left: 3px solid; border-radius: 4px; padding: 5px 8px;
    display: flex; align-items: center; justify-content: space-between; gap: 8px;
    flex: 1 1 0;
  }
  .tx-label {
    font-size: 11px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.04em; opacity: 0.6; white-space: nowrap; flex-shrink: 0;
  }
  .tx-value { font-size: 13px; font-family: monospace; line-height: 1.3; white-space: nowrap; text-align: right; }
  .tx-green  { border-color: #00ff88; background: rgba(0,255,136,0.07); color: #00ff88; }
  .tx-indigo { border-color: #00f0ff; background: rgba(0,240,255,0.07); color: #00f0ff; }
  .tx-amber  { border-color: #ffaa00; background: rgba(255,170,0,0.07); color: #ffaa00; }
  .tx-red    { border-color: #ff3366; background: rgba(255,51,102,0.07); color: #ff3366; }
  .tx-gray   { border-color: #6b7280; background: rgba(107,114,128,0.07); color: #6b7280; }
  .tx-purple { border-color: #bf5fff; background: rgba(191,95,255,0.07); color: #bf5fff; }

  /* full-width section */
  .full-section {
    padding: 20px; border-radius: 10px; margin-bottom: 12px;
    border: 1px solid #1a1a1a;
  }
  .full-section.discovery { border-left: 3px solid #00ff88; }
  .full-section.completion { border-left: 3px solid #00ff88; }
  .full-section.failed-wf { border-left: 3px solid #ff3366; }
  .full-section.cancelled-wf { border-left: 3px solid #555; }

  /* section label */
  .section-label {
    font-size: 12px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 1.5px; color: #666666; margin-bottom: 12px;
    padding-bottom: 8px; border-bottom: 1px solid #1a1a1a;
  }

  /* panels */
  .panel {
    background: #0a0a0a; border: 1px solid #1a1a1a; border-radius: 10px;
    padding: 1.25rem; margin-bottom: 1.25rem;
  }
  .panel-title {
    font-size: 0.88rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.08em; color: #666666; margin-bottom: 0.85rem;
  }
  .doc-label {
    font-size: 0.82rem; font-weight: 600; color: #666666;
    text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 0.4rem;
  }

  /* flow timeline */
  .flow-timeline { position: relative; }

  /* step nodes */
  .step-node {
    position: relative; margin-bottom: 0.75rem; padding: 0.85rem 1rem;
    background: #0a0a0a; border: 1px solid #1a1a1a; border-radius: 8px;
    animation: fadeIn 0.4s ease-out both;
  }
  .step-node.theme-submit  { border-left: 3px solid #00ff88; }
  .step-node.theme-sync    { border-left: 3px solid #bf5fff; }
  .step-node.theme-async   { border-left: 3px solid #ffaa00; }
  .step-node.theme-complete { border-left: 3px solid #00ff88; background: rgba(0,255,136,0.04); }
  .step-node.theme-fail    { border-left: 3px solid #ff3366; }
  .step-node.theme-engine  { border-left: 3px solid #00f0ff; }
  .step-node.theme-neutral { border-left: 3px solid #555; }

  .node-header {
    display: flex; align-items: center; gap: 0.5rem;
    margin-bottom: 0.3rem; flex-wrap: wrap;
  }
  .node-title { font-weight: 700; font-size: 1.05rem; color: #ffffff; }
  .node-desc { font-size: 0.92rem; color: #aaaaaa; }

  /* badges */
  .badge {
    font-size: 0.78rem; font-weight: 700; padding: 0.2em 0.6em;
    border-radius: 999px; text-transform: uppercase; letter-spacing: 0.04em;
    display: inline-flex; align-items: center; gap: 0.25em;
  }
  .badge.engine-action { background: rgba(0,240,255,0.15); color: #00f0ff; }
  .badge.lock-claim    { background: rgba(0,255,136,0.1); color: #00ff88; border: 1px solid rgba(0,255,136,0.3); }
  .badge.lock-release  { background: rgba(255,51,102,0.1); color: #ff3366; border: 1px solid rgba(255,51,102,0.3); }
  .badge.status-badge  { background: #111111; color: #aaaaaa; }
  .badge.fence-badge   { background: rgba(0,240,255,0.1); color: #00f0ff; }

  code, .mono {
    font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
    font-size: 0.92em;
  }

  /* retry sub-track */
  .retry-track { margin: 0.5rem 0 0.25rem 1rem; padding-left: 1rem; border-left: 2px dashed #ff3366; }
  .retry-item { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.35rem; font-size: 0.92rem; }
  .retry-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
  .retry-dot.fail { background: #ff3366; }
  .retry-dot.ok   { background: #00ff88; }

  /* poll sub-track */
  .poll-track { margin: 0.5rem 0 0.25rem 1rem; padding-left: 1rem; border-left: 2px dashed #ffaa00; }
  .poll-item { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.4rem; font-size: 0.92rem; }
  .poll-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .poll-dot.pending { background: #555; }
  .poll-dot.done    { background: #00ff88; }
  .poll-progress {
    background: #1a1a1a; border-radius: 4px; height: 6px; width: 80px;
    overflow: hidden; display: inline-block; vertical-align: middle; margin-left: 0.35rem;
  }
  .poll-progress-fill { height: 100%; border-radius: 4px; background: #ffaa00; }
  .poll-instance { font-size: 0.82rem; color: #666666; font-style: italic; }

  /* mongodb doc */
  .mongo-doc {
    background: #050505; border: 1px solid #1a1a1a; border-radius: 6px;
    padding: 0.85rem;
    font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
    font-size: 0.85rem; line-height: 1.7; overflow-x: auto; color: #aaaaaa;
  }
  .mongo-doc .key { color: #00f0ff; }
  .mongo-doc .str { color: #00d4ff; }
  .mongo-doc .num { color: #ffaa00; }
  .mongo-doc .kw  { color: #ff3366; }

  /* error traceback */
  .error-traceback {
    margin-top: 0.5rem;
  }
  .error-traceback summary {
    font-size: 0.82rem; font-weight: 600; color: #ff3366;
    cursor: pointer; user-select: none; list-style: none;
    display: flex; align-items: center; gap: 0.35rem;
  }
  .error-traceback summary::before {
    content: '\25B6'; font-size: 0.65rem; transition: transform 0.15s;
  }
  .error-traceback[open] summary::before { transform: rotate(90deg); }
  .error-traceback pre {
    background: #0a0000; border: 1px solid rgba(255,51,102,0.3); border-radius: 6px;
    padding: 0.75rem; margin-top: 0.4rem;
    font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
    font-size: 0.8rem; line-height: 1.5; overflow-x: auto;
    color: #ff6688; white-space: pre-wrap; word-break: break-word;
  }

  /* state transitions table */
  .state-table { width: 100%; border-collapse: collapse; font-size: 14px; margin-top: 12px; }
  .state-table th {
    text-align: left; font-size: 12px; text-transform: uppercase;
    letter-spacing: 0.5px; color: #666666; padding: 6px 10px;
    border-bottom: 1px solid #1a1a1a; font-weight: 600;
  }
  .state-table td { padding: 7px 10px; border-bottom: 1px solid #0a0a0a; color: #aaaaaa; vertical-align: top; }
  .state-table tr:last-child td { border-bottom: none; }
  .state-badge {
    display: inline-block; font-size: 12px; padding: 2px 8px;
    border-radius: 8px; font-weight: 600; font-family: monospace;
  }
  .s-pending   { background: rgba(107,114,128,0.15); color: #6b7280; border: 1px solid rgba(107,114,128,0.2); }
  .s-running   { background: rgba(0,240,255,0.08);   color: #00f0ff; border: 1px solid rgba(0,240,255,0.2); }
  .s-blocked   { background: rgba(255,170,0,0.08);   color: #ffaa00; border: 1px solid rgba(255,170,0,0.2); }
  .s-failed    { background: rgba(255,51,102,0.08);   color: #ff3366; border: 1px solid rgba(255,51,102,0.2); }
  .s-completed { background: rgba(0,255,136,0.08);   color: #00ff88; border: 1px solid rgba(0,255,136,0.2); }
  .s-review    { background: rgba(255,170,0,0.08);   color: #ffaa00; border: 1px solid rgba(255,170,0,0.2); }
  .s-cancelled { background: rgba(85,85,85,0.15);    color: #555;    border: 1px solid rgba(85,85,85,0.3); }

  /* dependency graph */
  .dep-graph {
    background: #0a0a0a; border: 1px solid #1a1a1a; border-radius: 10px;
    padding: 1.25rem; margin-bottom: 1.5rem;
  }
  .dep-graph .section-label { margin-bottom: 16px; }
  .dep-flow {
    display: flex; align-items: center; justify-content: flex-start; overflow-x: auto;
    padding: 1.5rem 1rem 0.5rem; gap: 0;
  }
  .dep-tier {
    display: flex; flex-direction: column; align-items: center; gap: 10px;
    position: relative; flex-shrink: 0; padding: 4px 0;
  }
  .dep-node {
    background: rgba(0,240,255,0.05); border: 1px solid rgba(0,240,255,0.2); border-radius: 8px;
    padding: 0.4em 0.8em 0.4em 2.8em; font-size: 0.85rem; font-weight: 600;
    color: #00f0ff; white-space: nowrap; text-align: right;
    overflow: hidden; text-overflow: ellipsis;
    width: 130px; min-width: 130px; max-width: 130px;
    font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
    position: relative; z-index: 1;
  }
  .dep-node:not(.terminal) { cursor: pointer; }
  .dep-node:not(.terminal):hover { border-color: #00f0ff; background: rgba(0,240,255,0.1); }
  .dep-node.terminal {
    background: #0a0a0a; border: 2px solid #555; border-radius: 20px;
    color: #aaaaaa; font-weight: 700; padding: 0.4em 0.8em;
    width: auto; min-width: 80px; max-width: none; text-align: center;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  }
  .dep-node.terminal.start-ok { border-color: #00ff88; color: #00ff88; background: rgba(0,255,136,0.05); }
  .dep-node.terminal.start-bad { border-color: #ff3366; color: #ff3366; background: rgba(255,51,102,0.05); }
  .dep-node.terminal.end-completed { border-color: #00ff88; color: #00ff88; background: rgba(0,255,136,0.05); }
  .dep-node.terminal.end-failed { border-color: #ff3366; color: #ff3366; background: rgba(255,51,102,0.05); }
  .dep-node.terminal.end-cancelled { border-color: #555; color: #555; background: rgba(85,85,85,0.08); }
  .dep-node.terminal.end-review { border-color: #ffaa00; color: #ffaa00; background: rgba(255,170,0,0.05); }
  /* connector: horizontal line between tiers */
  .dep-connector {
    width: 36px; height: 2px; background: #2a2a2a; flex-shrink: 0;
    position: relative;
  }
  .dep-connector::after {
    content: ''; position: absolute; right: -3px; top: -4px;
    border: 5px solid transparent; border-left: 6px solid #2a2a2a;
  }
  /* border around concurrent tiers */
  .dep-tier.concurrent {
    border: 1px solid rgba(255,255,255,0.08); border-left: 3px solid #666; border-radius: 10px;
    padding: 12px 10px;
  }
  /* lane groups: parallel chains rendered as horizontal rows */
  .dep-lane-group {
    border: 1px solid rgba(255,255,255,0.08); border-left: 3px solid #666; border-radius: 10px;
    padding: 12px 10px; display: flex; flex-direction: column;
    gap: 10px; flex-shrink: 0;
  }
  .dep-lane {
    display: grid; grid-template-columns: var(--lane-cols);
    align-items: center;
    border: 1px solid rgba(255,255,255,0.08); border-left: 3px solid #666; border-radius: 8px; padding: 10px 8px;
  }
  .dep-lane-fork {
    display: flex; flex-direction: column; align-items: stretch; gap: 10px;
    border: 1px solid rgba(255,255,255,0.08); border-left: 3px solid #666; border-radius: 8px; padding: 6px;
    box-sizing: border-box;
  }
  .dep-lane-fork .dep-node {
    width: auto; min-width: 0; max-width: none;
    box-sizing: border-box;
  }
  .dep-num {
    position: absolute; left: 0.5em; top: 50%; transform: translateY(-50%);
    color: #00f0ff;
    font-size: inherit; font-weight: 700;
  }
  /* sync/async step type — thick left border */
  .dep-node.mode-sync:not(.terminal)  { border-left: 3px solid #bf5fff; }
  .dep-node.mode-async:not(.terminal) { border-left: 3px solid #ffaa00; }
  .dep-node.state-pending.mode-sync:not(.terminal)  { border-left-color: rgba(191,95,255,0.4); }
  .dep-node.state-pending.mode-async:not(.terminal) { border-left-color: rgba(255,170,0,0.4); }

  /* state indicators on dependency graph nodes */
  .dep-state {
    font-size: 0.65rem; font-weight: 500; margin-top: 3px;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    letter-spacing: 0.3px;
  }
  .state-completed .dep-state { color: #00ff88; }
  .state-failed .dep-state { color: #ff3366; }
  .state-blocked .dep-state { color: #ffaa00; }
  .state-running .dep-state { color: #00f0ff; }
  .state-pending .dep-state { color: #555; }
  .state-needs-review .dep-state { color: #ffaa00; }
  /* grey-out unexecuted (pending) nodes */
  .dep-node.state-pending:not(.terminal) {
    background: #0a0a0a; border-color: #1a1a1a; color: #333; opacity: 0.55;
  }
  .dep-node.state-pending:not(.terminal) .dep-num {
    color: #333;
  }

  /* step dependency info */
  .dep-info {
    font-size: 0.85rem; color: #666666; margin-bottom: 0.5rem;
    display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap;
  }
  .dep-info code {
    background: rgba(0,240,255,0.08); padding: 0.1em 0.4em; border-radius: 3px;
    color: #00f0ff; font-size: 0.85em;
  }
  .dep-info .root-tag {
    background: rgba(0,255,136,0.1); color: #00ff88; padding: 0.15em 0.5em;
    border-radius: 999px; font-size: 0.82rem; font-weight: 600;
  }

  /* parallel group wrapper */
  .parallel-group {
    border: 1px solid rgba(255,255,255,0.08); border-left: 3px solid #666; border-radius: 12px;
    padding: 16px 20px; margin: 20px 0;
  }
  .parallel-group-label {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 1.5px; color: #666; margin-bottom: 12px;
    display: flex; align-items: center; gap: 0.5rem;
  }
  .parallel-group-label::after {
    content: ''; flex: 1; height: 1px; background: rgba(255,255,255,0.08);
  }
  .parallel-lane {
    border: 1px solid rgba(255,255,255,0.08); border-left: 3px solid #666; border-radius: 8px;
    padding: 12px 16px; margin-bottom: 10px;
  }
  .parallel-lane:last-child { margin-bottom: 0; }
  .parallel-lane-tier {
    border: 1px solid rgba(255,255,255,0.08); border-left: 3px solid #666; border-radius: 8px;
    padding: 8px 12px; margin-bottom: 8px;
  }
  .parallel-lane-tier:last-child { margin-bottom: 0; }

  /* fade-in */
  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(8px); }
    to   { opacity: 1; transform: translateY(0); }
  }
"""

for _i in range(1, 21):
    CSS += f"  .step-node:nth-child({_i})  {{ animation-delay: {_i * 0.05:.2f}s; }}\n"


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------


def _esc(v: object) -> str:
    return html.escape(str(v))


def _mongo_val(v: object, indent: int = 2) -> str:
    """Render a Python value as syntax-highlighted mongo-doc HTML."""
    prefix = "  " * indent
    if isinstance(v, dict):
        if not v:
            return "{}"
        inner = []
        for k, val in v.items():
            rendered = _mongo_val(val, indent + 1)
            inner.append(f'{prefix}  <span class="key">"{_esc(k)}"</span>: {rendered}')
        return "{\n" + ",\n".join(inner) + f"\n{prefix}}}"
    if isinstance(v, bool):
        return f'<span class="kw">{"true" if v else "false"}</span>'
    if v is None:
        return '<span class="kw">null</span>'
    if isinstance(v, int | float):
        return f'<span class="num">{v}</span>'
    if isinstance(v, str):
        return f'<span class="str">"{_esc(v)}"</span>'
    if isinstance(v, list):
        if not v:
            return "[]"
        inner = [f"{prefix}  {_mongo_val(item, indent + 1)}" for item in v]
        return "[\n" + ",\n".join(inner) + f"\n{prefix}]"
    return _esc(v)


def _mongo_doc(fields: dict[str, Any]) -> str:
    """Render a dict as a full mongo-doc <pre> block."""
    if not fields:
        return "<pre>{}</pre>"
    lines = []
    items = list(fields.items())
    for i, (k, v) in enumerate(items):
        rendered = _mongo_val(v, 1)
        comma = "," if i < len(items) - 1 else ""
        lines.append(f'  <span class="key">"{_esc(k)}"</span>: {rendered}{comma}')
    return "<pre>{\n" + "\n".join(lines) + "\n}</pre>"


def _tx(cls: str, label: str, value: str) -> str:
    return (
        f'        <div class="tx-block tx-{cls}">\n'
        f'          <div class="tx-label">{_esc(label)}</div>\n'
        f'          <div class="tx-value">{value}</div>\n'
        f"        </div>\n"
    )


def _fmt_ts(ts: datetime | None) -> str:
    """Format a timestamp for display."""
    if ts is None:
        return "?"
    return ts.strftime("%H:%M:%S.%f")[:-3]


def _badge(cls: str, text: str) -> str:
    return f'<span class="badge {cls}">{_esc(text)}</span>'


_ERROR_TRUNCATE_LEN = 80


def _truncate(s: str | None, max_len: int = _ERROR_TRUNCATE_LEN) -> str | None:
    if s is None:
        return None
    return s[:max_len] + "..." if len(s) > max_len else s


def _fail_node(title: str, desc: str, traceback: str | None = None) -> str:
    """Render a failure node with an optional collapsible stack trace."""
    tb_html = ""
    if traceback:
        tb_html = (
            '            <details class="error-traceback">\n'
            "              <summary>Stack trace</summary>\n"
            f"              <pre>{_esc(traceback)}</pre>\n"
            "            </details>\n"
        )
    return (
        '          <div class="step-node theme-fail">\n'
        '            <div class="node-header">\n'
        f'              <span class="node-title">{_esc(title)}</span>\n'
        f'              {_badge("status-badge", "FAILED")}\n'
        f"            </div>\n"
        f'            <div class="node-desc">{_esc(desc)}</div>\n'
        + tb_html
        + "          </div>\n"
    )


# ---------------------------------------------------------------------------
# Event grouping
# ---------------------------------------------------------------------------

_WORKFLOW_EVENTS = frozenset({
    AuditEventType.WORKFLOW_CREATED,
    AuditEventType.WORKFLOW_COMPLETED,
    AuditEventType.WORKFLOW_FAILED,
    AuditEventType.WORKFLOW_CANCELLED,
    AuditEventType.SWEEP_ANOMALY,
})


def _group_events(
    events: list[AuditEvent],
) -> tuple[list[AuditEvent], dict[int, list[AuditEvent]]]:
    """Split events into workflow-level and per-step groups."""
    workflow_events: list[AuditEvent] = []
    step_events: dict[int, list[AuditEvent]] = defaultdict(list)

    for e in events:
        if e.event_type in _WORKFLOW_EVENTS or e.step_index is None:
            workflow_events.append(e)
        else:
            step_events[e.step_index].append(e)

    return workflow_events, dict(step_events)


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------


def _extract_dep_info(
    step_groups: dict[int, list[AuditEvent]],
    workflow: Workflow | None = None,
) -> dict[str, list[str]]:
    """Build step_name -> depends_on mapping.

    When *workflow* is provided the full step graph is used, so unexecuted
    steps appear in the dependency map.  Falls back to extracting from
    audit events when the workflow object is not available.
    """
    if workflow is not None:
        deps: dict[str, list[str]] = {}
        for step in workflow.steps:
            if step.depends_on is not None:
                deps[step.name] = step.depends_on
        return deps

    deps = {}
    for idx in sorted(step_groups.keys()):
        for e in step_groups[idx]:
            if e.step_name and e.step_depends_on is not None:
                deps[e.step_name] = e.step_depends_on
                break
    return deps


def _compute_tiers(
    step_groups: dict[int, list[AuditEvent]],
    dep_map: dict[str, list[str]],
    all_steps: list[tuple[int, str]] | None = None,
) -> list[list[tuple[int, str]]]:
    """Compute concurrency tiers — groups of (idx, name) that can run in parallel.

    Args:
        step_groups: Per-step audit event groups (only executed steps).
        dep_map: Step name to dependency list mapping.
        all_steps: When provided (from a Workflow object), the complete
            list of ``(index, name)`` pairs for every step in the
            workflow — including those that have not yet executed.

    Returns:
        A list of tiers, each tier is a list of (step_index, step_name) pairs.
    """
    # Build the authoritative name→idx map.  Prefer all_steps when available
    # so that unexecuted steps are included.
    name_to_idx: dict[str, int] = {}
    if all_steps is not None:
        for idx, name in all_steps:
            name_to_idx[name] = idx
    else:
        for idx in sorted(step_groups.keys()):
            if step_groups[idx]:
                name = step_groups[idx][0].step_name or f"step_{idx}"
                name_to_idx[name] = idx

    if not dep_map:
        # No dependency info — return sequential tiers
        if all_steps is not None:
            return [[(idx, name)] for idx, name in all_steps]
        result = []
        for idx in sorted(step_groups.keys()):
            name = (step_groups[idx][0].step_name or f"step_{idx}") if step_groups[idx] else f"step_{idx}"
            result.append([(idx, name)])
        return result

    # Compute depth of each step in the DAG
    depths: dict[str, int] = {}

    def _depth(name: str) -> int:
        if name in depths:
            return depths[name]
        parents = dep_map.get(name, [])
        if not parents:
            depths[name] = 0
        else:
            depths[name] = max(_depth(p) for p in parents if p in dep_map) + 1
        return depths[name]

    for name in dep_map:
        _depth(name)

    # Also include steps not in dep_map (no dependency info) at sequential depths
    all_names: list[str]
    if all_steps is not None:
        all_names = [name for _idx, name in all_steps]
    else:
        all_names = [
            (step_groups[idx][0].step_name or f"step_{idx}")
            for idx in sorted(step_groups.keys())
            if step_groups[idx]
        ]
    max_depth = max(depths.values()) if depths else -1
    for name in all_names:
        if name not in depths:
            max_depth += 1
            depths[name] = max_depth

    # Group by depth
    tier_map: dict[int, list[tuple[int, str]]] = {}
    for name, depth in depths.items():
        step_idx = name_to_idx.get(name)
        if step_idx is not None:
            tier_map.setdefault(depth, []).append((step_idx, name))

    # Sort tiers by depth, and within each tier by step index
    return [sorted(tier_map[d]) for d in sorted(tier_map.keys())]


def _compute_lane_groups(
    dep_map: dict[str, list[str]],
    tiers: list[list[tuple[int, str]]],
) -> list[tuple[str, list[Any]]]:
    """Detect consecutive parallel tiers that form independent lanes.

    Lanes support nested parallelism: a single lane can contain sub-tiers
    with multiple items (fan-out within a lane).  The extension stops when
    any step in the next tier depends on steps in *multiple* lanes.

    Returns a list of:
        ("single", [(idx, name)])                          — single-step tier
        ("parallel", [(idx, name), ...])                   — parallel tier not forming lanes
        ("lanes", [[sub_tier, ...], ...])                  — lane groups with sub-tiers
            where each sub_tier is [(idx, name), ...]
    """
    result: list[tuple[str, list[Any]]] = []
    i = 0
    while i < len(tiers):
        tier = tiers[i]
        if len(tier) == 1:
            result.append(("single", tier))
            i += 1
            continue

        # Each lane is a list of sub-tiers; first sub-tier has one item.
        lanes: list[list[list[tuple[int, str]]]] = [[[item]] for item in tier]
        step_to_lane: dict[str, int] = {item[1]: li for li, item in enumerate(tier)}

        j = i + 1
        while j < len(tiers):
            next_tier = tiers[j]
            # Map each step to exactly one lane via its dependencies.
            tier_assignments: dict[int, list[tuple[int, str]]] = {}
            valid = True
            for item in next_tier:
                _idx, name = item
                deps = dep_map.get(name, [])
                dep_lanes = {step_to_lane[d] for d in deps if d in step_to_lane}
                if len(dep_lanes) != 1:
                    valid = False
                    break
                lane_idx = dep_lanes.pop()
                tier_assignments.setdefault(lane_idx, []).append(item)

            if not valid:
                break

            # Extend each lane with its new sub-tier.
            for lane_idx, items in tier_assignments.items():
                lanes[lane_idx].append(items)
                for _idx, name in items:
                    step_to_lane[name] = lane_idx
            j += 1

        if j > i + 1:
            result.append(("lanes", lanes))
            i = j
        else:
            result.append(("parallel", tier))
            i += 1

    return result


def _compute_step_states(
    step_groups: dict[int, list[AuditEvent]],
    workflow: Workflow | None = None,
) -> dict[str, str]:
    """Determine the final state of each step from audit events.

    When *workflow* is provided, every step is seeded as ``"pending"``
    so that unexecuted steps appear in the dependency graph.
    """
    states: dict[str, str] = {}

    # Seed with all workflow steps so unexecuted ones default to "pending"
    if workflow is not None:
        for step in workflow.steps:
            states[step.name] = "pending"

    for events in step_groups.values():
        name = events[0].step_name if events else None
        if not name:
            continue
        state = "pending"
        for e in events:
            if e.event_type == AuditEventType.STEP_COMPLETED:
                state = "completed"
            elif e.event_type == AuditEventType.STEP_FAILED:
                state = "failed"
            elif e.event_type == AuditEventType.RECOVERY_NEEDS_REVIEW:
                state = "needs-review"
            elif e.event_type == AuditEventType.STEP_BLOCKED and state == "pending":
                state = "blocked"
            elif e.event_type == AuditEventType.STEP_RUNNING and state == "pending":
                state = "running"
        states[name] = state
    return states


def _compute_step_modes(
    step_groups: dict[int, list[AuditEvent]],
    workflow: Workflow | None = None,
) -> dict[str, bool]:
    """Build a step_name -> is_async mapping for dependency graph markers."""
    modes: dict[str, bool] = {}
    if workflow is not None:
        for step in workflow.steps:
            modes[step.name] = step.is_async
    for events in step_groups.values():
        for e in events:
            if e.step_name and e.is_async is not None:
                modes[e.step_name] = e.is_async
                break
    return modes


def _dep_node(
    cls: str,
    name: str,
    state: str,
    step_num: int | None = None,
    is_async: bool | None = None,
) -> str:
    """Render a single dependency graph node with state indicator."""
    state_cls = f" state-{state}"
    label = "needs review" if state == "needs-review" else state
    num_html = f'<span class="dep-num">{step_num}</span> ' if step_num is not None else ""
    mode_cls = " mode-async" if is_async else (" mode-sync" if is_async is not None else "")
    anchor = f' onclick="document.getElementById(\'step-{_esc(name)}\')?.scrollIntoView({{behavior:\'smooth\',block:\'center\'}})"'
    return (
        f'<div class="{cls}{state_cls}{mode_cls}"{anchor}>{num_html}{_esc(name)}'
        f'<div class="dep-state">&rarr; {label}</div></div>\n'
    )


def _render_dependency_graph(
    dep_map: dict[str, list[str]],
    tiers: list[list[tuple[int, str]]],
    step_states: dict[str, str] | None = None,
    wf_state: str = "pending",
    step_modes: dict[str, bool] | None = None,
) -> str:
    """Render a visual dependency graph as a horizontal flow diagram."""
    if not tiers:
        return ""

    states = step_states or {}
    modes = step_modes or {}
    groups = _compute_lane_groups(dep_map, tiers)
    parts = ['<div class="dep-graph">\n  <div class="section-label">Dependency Graph</div>\n']
    parts.append('  <div class="dep-flow">\n')

    # Start node — green if any step progressed, red if workflow failed without progress
    any_progress = any(s != "pending" for s in states.values())
    start_cls = " start-ok" if any_progress else (" start-bad" if wf_state in ("failed", "cancelled") else "")
    parts.append('    <div class="dep-tier">\n')
    parts.append(f'      <div class="dep-node terminal{start_cls}">START</div>\n')
    parts.append("    </div>\n")

    for group_type, group_data in groups:
        parts.append('    <div class="dep-connector"></div>\n')

        if group_type == "single":
            idx, name = group_data[0]
            parts.append('    <div class="dep-tier">\n      ')
            parts.append(_dep_node("dep-node", name, states.get(name, "pending"), idx + 1, is_async=modes.get(name)))
            parts.append("    </div>\n")

        elif group_type == "parallel":
            parts.append('    <div class="dep-tier concurrent">\n')
            for idx, name in group_data:
                parts.append("      ")
                parts.append(_dep_node("dep-node", name, states.get(name, "pending"), idx + 1, is_async=modes.get(name)))
            parts.append("    </div>\n")

        elif group_type == "lanes":
            max_depth = max(len(lane) for lane in group_data)
            cols = " ".join(["130px 36px"] * (max_depth - 1) + ["130px"])
            parts.append(f'    <div class="dep-lane-group" style="--lane-cols: {cols}">\n')
            for lane in group_data:
                parts.append('      <div class="dep-lane">\n')
                for k, sub_tier in enumerate(lane):
                    if k > 0:
                        parts.append('        <div class="dep-connector"></div>\n')
                    if len(sub_tier) == 1:
                        idx, name = sub_tier[0]
                        parts.append("        ")
                        parts.append(_dep_node("dep-node", name, states.get(name, "pending"), idx + 1, is_async=modes.get(name)))
                    else:
                        parts.append('        <div class="dep-lane-fork">\n')
                        for idx, name in sub_tier:
                            parts.append("          ")
                            parts.append(_dep_node("dep-node", name, states.get(name, "pending"), idx + 1, is_async=modes.get(name)))
                        parts.append("        </div>\n")
                parts.append("      </div>\n")
            parts.append("    </div>\n")

    # End node — styled to reflect workflow outcome
    end_cls_map = {
        "completed": "end-completed",
        "failed": "end-failed",
        "cancelled": "end-cancelled",
        "needs_review": "end-review",
    }
    end_cls = end_cls_map.get(wf_state, "")
    end_extra = f" {end_cls}" if end_cls else ""
    parts.append('    <div class="dep-connector"></div>\n')
    parts.append('    <div class="dep-tier">\n')
    parts.append(f'      <div class="dep-node terminal{end_extra}">END</div>\n')
    parts.append("    </div>\n")

    parts.append("  </div>\n</div>\n")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Workflow state helper
# ---------------------------------------------------------------------------

_WF_TERMINAL_EVENTS = {
    AuditEventType.WORKFLOW_COMPLETED: "completed",
    AuditEventType.WORKFLOW_FAILED: "failed",
    AuditEventType.WORKFLOW_CANCELLED: "cancelled",
}


def _workflow_final_state(wf_events: list[AuditEvent]) -> str:
    """Determine the final workflow state from workflow-level events."""
    for e in reversed(wf_events):
        state = _WF_TERMINAL_EVENTS.get(e.event_type)
        if state:
            return state
        if e.event_type == AuditEventType.RECOVERY_NEEDS_REVIEW:
            return "needs_review"
    return "pending"


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------


def _render_header(wf_name: str) -> str:
    return (
        f'<div class="page-header">\n'
        f"  <h1>{_esc(wf_name)} &mdash; Execution Report</h1>\n"
        f'  <div class="subtitle">Generated from audit log</div>\n'
        f"</div>\n"
    )


def _render_summary(events: list[AuditEvent], wf_name: str) -> str:
    """Render summary banner with workflow status, duration, instances."""
    if not events:
        return ""

    final_status = _workflow_final_state(events)
    status_cls_map = {"completed": "completed", "failed": "failed", "needs_review": "review"}
    status_cls = status_cls_map.get(final_status, "neutral")

    # Collect stats
    instances = sorted({e.instance_id for e in events if e.instance_id})
    step_indices = sorted({e.step_index for e in events if e.step_index is not None})
    duration_s = (events[-1].timestamp - events[0].timestamp).total_seconds()

    stats = [
        f'<span class="stat {status_cls}">{_esc(final_status)}</span>',
        f'<span class="stat neutral">{len(step_indices)} steps</span>',
        f'<span class="stat neutral">{duration_s:.1f}s</span>',
        f'<span class="stat neutral">{len(instances)} instance{"s" if len(instances) != 1 else ""}</span>',
    ]

    return (
        f'<div class="summary-banner">\n'
        f'  <div class="wf-name">Workflow: <code>{_esc(wf_name)}</code></div>\n'
        f'  <div class="summary-stats">{"".join(stats)}</div>\n'
        f"</div>\n"
    )


def _render_discovery(
    step_groups: dict[int, list[AuditEvent]],
) -> str:
    """Render the discovery/claim section from the first STEP_CLAIMED event."""
    # Find the earliest STEP_CLAIMED across all step groups
    claim: AuditEvent | None = None
    for idx in sorted(step_groups.keys()):
        for e in step_groups[idx]:
            if e.event_type == AuditEventType.STEP_CLAIMED:
                if claim is None or e.timestamp < claim.timestamp:
                    claim = e
                break  # only need first claim per step group
    if claim is None:
        return ""

    step_label = claim.step_name or f"step_{claim.step_index}"
    flow = (
        '      <div class="step-flow-panel">\n'
        '        <div class="section-label">Start</div>\n'
        '        <div class="flow-timeline">\n'
        '          <div class="step-node theme-engine">\n'
        '            <div class="node-header">\n'
        f'              <span class="node-title">try_claim_step({_esc(step_label)})</span>\n'
        f'              {_badge("lock-claim", "step lock acquired")}\n'
        f'              {_badge("fence-badge", f"fence_token {chr(8594)} {claim.fence_token}")}\n'
        f"            </div>\n"
        f'            <div class="node-desc">'
        f'Claimed by <code>{_esc(claim.instance_id)}</code> at {_fmt_ts(claim.timestamp)}</div>\n'
        f"          </div>\n"
        f"        </div>\n"
        f"      </div>\n"
    )

    txs = (
        _tx("purple", "Workflow", "pending &rarr; running")
        + _tx("green", "Lock", f"claim (fence &rarr; {claim.fence_token})")
        + _tx("indigo", "Fence Token", f"fence_token &rarr; {claim.fence_token}")
    )
    tx_col = f'      <div class="step-transitions">\n{txs}      </div>\n'

    doc_fields = {
        f"steps[{claim.step_index}].fence_token": claim.fence_token,
        f"steps[{claim.step_index}].locked_by": claim.instance_id,
        "status": "running",
    }
    doc = (
        '      <div class="step-doc">\n'
        '        <div class="panel">\n'
        '          <div class="panel-title">Start Workflow</div>\n'
        f'          <div class="mongo-doc">{_mongo_doc(doc_fields)}</div>\n'
        "        </div>\n"
        "      </div>\n"
    )

    return f'    <div class="step-section discovery">\n{flow}{tx_col}{doc}    </div>\n'


def _render_flow_nodes(
    idx: int,
    step_handler: str,
    is_async: bool,
    label: str,
    dep_html: str,
    recovery: list[AuditEvent],
    submitted: list[AuditEvent],
    running: list[AuditEvent],
    blocked: list[AuditEvent],
    completed: list[AuditEvent],
    failed: list[AuditEvent],
    polls: list[AuditEvent],
    poll_timeout: list[AuditEvent],
    poll_max: list[AuditEvent],
    poll_check_errors: list[AuditEvent],
) -> str:
    """Build the flow-timeline panel with all step nodes."""
    nodes = []

    # Recovery nodes
    for rev in recovery:
        action = rev.recovery_action or "unknown"
        nodes.append(
            '          <div class="step-node theme-engine">\n'
            '            <div class="node-header">\n'
            f'              <span class="node-title">Recovery</span>\n'
            f'              {_badge("engine-action", action)}\n'
            f"            </div>\n"
            f'            <div class="node-desc">Recovery at {_fmt_ts(rev.timestamp)}</div>\n'
            f"          </div>\n"
        )

    # Write-Ahead (SUBMITTED)
    if submitted:
        e = submitted[0]
        nodes.append(
            '          <div class="step-node theme-submit">\n'
            '            <div class="node-header">\n'
            f'              <span class="node-title">Write-Ahead</span>\n'
            f'              {_badge("status-badge", "SUBMITTED")}\n'
            f"            </div>\n"
            f'            <div class="node-desc">'
            f"<code>steps[{idx}].status &rarr; \"submitted\"</code> at {_fmt_ts(e.timestamp)}</div>\n"
            f"          </div>\n"
        )

    # Handler execution (RUNNING events = retry attempts)
    if running:
        theme = "theme-async" if is_async else "theme-sync"
        exec_badge = "async submit" if is_async else "sync exec"
        max_att = running[0].max_attempts or len(running)
        has_retries = len(running) > 1

        handler_node = (
            f'          <div class="step-node {theme}">\n'
            f'            <div class="node-header">\n'
            f'              <span class="node-title">{_esc(step_handler)}()</span>\n'
            f'              {_badge("engine-action", exec_badge)}\n'
            f"            </div>\n"
            f'            <div class="node-desc">Attempt {running[-1].attempt or 1}/{max_att} at {_fmt_ts(running[-1].timestamp)}</div>\n'
        )

        if has_retries:
            handler_node += '            <div class="retry-track">\n'
            for ri, r_evt in enumerate(running):
                is_last_running = ri == len(running) - 1
                is_fail = not is_last_running or bool(failed)
                dot_cls = "fail" if is_fail else "ok"
                color = "#f87171" if is_fail else "#34d399"
                label_text = "failed" if is_fail else "success"
                handler_node += (
                    f'              <div class="retry-item">'
                    f'<span class="retry-dot {dot_cls}"></span>'
                    f'<span style="color:{color};">Attempt {r_evt.attempt or ri + 1}</span>'
                    f'<span style="color:#6b7280;"> &mdash; {label_text}</span></div>\n'
                )
            handler_node += "            </div>\n"

        handler_node += "          </div>\n"
        nodes.append(handler_node)

    # BLOCKED (async)
    if blocked:
        e = blocked[0]
        nodes.append(
            '          <div class="step-node theme-async">\n'
            '            <div class="node-header">\n'
            f'              <span class="node-title">BLOCKED</span>\n'
            f'              {_badge("lock-release", "lock released")}\n'
            f"            </div>\n"
            f'            <div class="node-desc">'
            f"Lock released at {_fmt_ts(e.timestamp)}. Polling scheduled.</div>\n"
            f"          </div>\n"
        )

    # Poll sub-track
    if polls:
        poll_items = []
        for pi, p_evt in enumerate(polls):
            is_last = pi == len(polls) - 1
            is_done = is_last and bool(completed)
            dot_cls = "done" if is_done else "pending"
            pct = f"{p_evt.poll_progress:.0%}" if p_evt.poll_progress is not None else "?"
            pbar_color = " background:#34d399;" if is_done else ""
            pbar_width = f"{p_evt.poll_progress * 100:.0f}%" if p_evt.poll_progress is not None else "0%"

            poll_items.append(
                f'              <div class="poll-item"{"" if pi == 0 else " style=\"margin-top:0.3rem;\""}'
                f'><span class="poll-dot {dot_cls}"></span>'
                f"<span><strong>Poll {p_evt.poll_count or pi + 1}</strong></span>"
                f' {_badge("fence-badge", f"fence {chr(8594)} {p_evt.fence_token}")}'
                f'<span class="poll-instance">{_esc(p_evt.instance_id or "?")}</span></div>\n'
                f'              <div class="poll-item" style="padding-left:1rem;">'
                f'<span style="color:#9ca3af;">completeness_check &rarr;</span>'
                f'<span style="color:{"#34d399" if is_done else "#fbbf24"};">{pct}</span>'
                f'<div class="poll-progress"><div class="poll-progress-fill" style="width:{pbar_width};{pbar_color}"></div></div>'
                + ("" if is_done else f' {_badge("lock-release", "release")}')
                + "</div>\n"
            )

        poll_html = "".join(poll_items)
        nodes.append(
            '          <div class="step-node theme-engine">\n'
            '            <div class="node-header">\n'
            f'              <span class="node-title">Claim-Poll-Release Cycle</span>\n'
            f'              {_badge("engine-action", "engine loop")}\n'
            f"            </div>\n"
            f'            <div class="node-desc">{len(polls)} poll(s)</div>\n'
            f'            <div class="poll-track">\n{poll_html}'
            f"            </div>\n"
            f"          </div>\n"
        )

    # Poll timeout / max exceeded
    nodes += [_fail_node("Poll Timeout", pt.error or "Poll timeout", pt.error_traceback) for pt in poll_timeout]
    nodes += [_fail_node("Max Polls Exceeded", pm.error or "Max polls exceeded", pm.error_traceback) for pm in poll_max]
    nodes += [_fail_node("Check Errors Exceeded", pce.error or "Check errors exceeded", pce.error_traceback) for pce in poll_check_errors]

    # Completion or failure
    if completed:
        e = completed[0]
        fence_text = f"fence_token: {e.fence_token}" if e.fence_token else ""
        badges = _badge("status-badge", "COMPLETED")
        if fence_text:
            badges += f" {_badge('fence-badge', fence_text)}"
        nodes.append(
            '          <div class="step-node theme-complete">\n'
            '            <div class="node-header">\n'
            f'              <span class="node-title">Advance</span>\n'
            f"              {badges}\n"
            f"            </div>\n"
            f'            <div class="node-desc">'
            f"Step completed at {_fmt_ts(e.timestamp)}</div>\n"
            f"          </div>\n"
        )
    elif failed:
        e = failed[0]
        nodes.append(_fail_node("Failed", e.error or "Step failed", e.error_traceback))

    return (
        f'      <div class="step-flow-panel">\n'
        f'        <div class="section-label">{label}</div>\n'
        + (f"        {dep_html}" if dep_html else "")
        + '        <div class="flow-timeline">\n'
        + "".join(nodes)
        + "        </div>\n      </div>\n"
    )


def _render_step_transitions(
    step_events: list[AuditEvent],
    is_async: bool,
    step_num: int,
    submitted: list[AuditEvent],
    running: list[AuditEvent],
    blocked: list[AuditEvent],
    completed: list[AuditEvent],
    failed: list[AuditEvent],
    advanced: list[AuditEvent],
    polls: list[AuditEvent],
    poll_timeout: list[AuditEvent],
    poll_max: list[AuditEvent],
    poll_check_errors: list[AuditEvent],
) -> str:
    """Build the chronological transition column for a step."""
    txs = []
    claim_events = [e for e in step_events if e.event_type == AuditEventType.STEP_CLAIMED]

    # Phase 1: Initial claim → submit → handler → block/complete
    if claim_events:
        txs.append(_tx("green", "Lock", f"claim (fence &rarr; {claim_events[0].fence_token})"))

    if submitted:
        txs.append(_tx("indigo", "Step Status", "&rarr; submitted"))

    if is_async:
        txs.append(_tx("amber", "Handler", "async submit"))
    elif running:
        txs.append(_tx("indigo", "Handler", "sync exec"))

    if len(running) > 1:
        n_fails = len(running) - (1 if completed else 0)
        txs.append(_tx("red", "Retries", f"{n_fails} {'retry' if n_fails == 1 else 'retries'}"))

    if blocked:
        txs.append(_tx("amber", "Step Status", "&rarr; blocked"))
        txs.append(_tx("red", "Lock", "released"))

    # Phase 2: Poll cycles — each is a claim → poll → release
    for pi, p_evt in enumerate(polls):
        is_last = pi == len(polls) - 1
        is_done = is_last and bool(completed)
        fence = p_evt.fence_token or (claim_events[0].fence_token or 0) + pi + 1
        pct = f"{p_evt.poll_progress:.0%}" if p_evt.poll_progress is not None else "?"
        txs.append(_tx("green", "Lock", f"claim (fence &rarr; {fence})"))
        txs.append(_tx("amber", f"Poll {p_evt.poll_count or pi + 1}", f"check &rarr; {pct}"))
        if not is_done:
            txs.append(_tx("red", "Lock", "released"))

    # Phase 2b: Poll failure diagnostics
    txs.extend(_tx("red", "Poll", "timeout") for _ in poll_timeout)
    txs.extend(_tx("red", "Poll", "max exceeded") for _ in poll_max)
    txs.extend(_tx("red", "Poll", "check errors exceeded") for _ in poll_check_errors)

    # Phase 3: Terminal state
    if completed:
        txs.append(_tx("green", "Step Status", "&rarr; completed"))
        txs.append(_tx("red", "Lock", "released"))
    elif failed:
        txs.append(_tx("red", "Step Status", "&rarr; failed"))
        txs.append(_tx("red", "Lock", "released"))

    if advanced:
        txs.append(_tx("purple", "Step Index", f"idx &rarr; {step_num}"))

    return f'      <div class="step-transitions">\n{"".join(txs)}      </div>\n'


def _render_step_doc_panel(
    idx: int,
    step_name: str,
    step_num: int,
    completed: list[AuditEvent],
    failed: list[AuditEvent],
) -> str:
    """Build the MongoDB document diff panel for a step."""
    doc_fields: dict[str, Any] = {}
    final = completed[0] if completed else (failed[0] if failed else None)
    if final and final.result_summary:
        if final.fence_token:
            doc_fields["fence_token"] = final.fence_token
        doc_fields[f"steps[{idx}]"] = {
            "name": step_name,
            "result": final.result_summary,
            "status": "completed" if completed else "failed",
        }
    elif final and final.error:
        step_doc: dict[str, Any] = {
            "name": step_name,
            "status": "failed",
            "error": _truncate(final.error),
        }
        if final.error_traceback:
            step_doc["error_traceback"] = _truncate(final.error_traceback, 500)
        doc_fields[f"steps[{idx}]"] = step_doc

    return (
        '      <div class="step-doc">\n'
        '        <div class="panel">\n'
        f'          <div class="panel-title">After Step {step_num} &mdash; {_esc(step_name)}</div>\n'
        f'          <div class="mongo-doc">{_mongo_doc(doc_fields)}</div>\n'
        "        </div>\n"
        "      </div>\n"
    )


def _render_step_section(
    idx: int,
    step_events: list[AuditEvent],
) -> str:
    """Render a single step's 3-column section from its audit events."""
    if not step_events:
        return ""

    step_name = step_events[0].step_name or f"step_{idx}"
    step_handler = step_events[0].step_handler or "unknown"
    is_async = any(e.is_async for e in step_events)
    step_num = idx + 1

    # Classify events (single pass)
    by_type: dict[AuditEventType, list[AuditEvent]] = {}
    recovery: list[AuditEvent] = []
    for e in step_events:
        if e.event_type.value.startswith("recovery_"):
            recovery.append(e)
        by_type.setdefault(e.event_type, []).append(e)
    submitted = by_type.get(AuditEventType.STEP_SUBMITTED, [])
    running = by_type.get(AuditEventType.STEP_RUNNING, [])
    completed = by_type.get(AuditEventType.STEP_COMPLETED, [])
    failed = by_type.get(AuditEventType.STEP_FAILED, [])
    blocked = by_type.get(AuditEventType.STEP_BLOCKED, [])
    polls = by_type.get(AuditEventType.POLL_CHECKED, [])
    poll_timeout = by_type.get(AuditEventType.POLL_TIMEOUT, [])
    poll_max = by_type.get(AuditEventType.POLL_MAX_EXCEEDED, [])
    poll_check_errors = by_type.get(AuditEventType.POLL_CHECK_ERRORS_EXCEEDED, [])
    advanced = by_type.get(AuditEventType.STEP_ADVANCED, [])

    # --- Dependency info ---
    step_deps: list[str] | None = None
    for e in step_events:
        if e.step_depends_on is not None:
            step_deps = e.step_depends_on
            break

    dep_html = ""
    if step_deps is not None:
        if len(step_deps) == 0:
            dep_html = '<div class="dep-info"><span class="root-tag">root step</span> no dependencies</div>\n'
        else:
            dep_names = ", ".join(f"<code>{_esc(d)}</code>" for d in step_deps)
            dep_html = f'<div class="dep-info">Depends on: {dep_names}</div>\n'

    # --- Build the three columns ---
    mode = "async" if is_async else "sync"
    label = f"Step {step_num} &mdash; {_esc(step_name)} ({mode})"

    flow_panel = _render_flow_nodes(
        idx, step_handler, is_async, label, dep_html,
        recovery, submitted, running, blocked, completed, failed,
        polls, poll_timeout, poll_max, poll_check_errors,
    )
    tx_col = _render_step_transitions(
        step_events, is_async, step_num,
        submitted, running, blocked, completed, failed, advanced,
        polls, poll_timeout, poll_max, poll_check_errors,
    )
    doc = _render_step_doc_panel(idx, step_name, step_num, completed, failed)

    step_cls = "step-section async-step" if is_async else "step-section sync-step"
    return f'    <div class="{step_cls}" id="step-{_esc(step_name)}">\n{flow_panel}{tx_col}{doc}    </div>\n'


def _render_completion(wf_events: list[AuditEvent]) -> str:
    """Render the workflow completion or failure section."""
    completed = next(
        (e for e in wf_events if e.event_type == AuditEventType.WORKFLOW_COMPLETED), None
    )
    failed = next(
        (e for e in wf_events if e.event_type == AuditEventType.WORKFLOW_FAILED), None
    )

    if completed:
        fence_badge = (
            f'          {_badge("fence-badge", f"fence_token: {completed.fence_token}")}\n'
            if completed.fence_token else ""
        )
        return (
            '<div class="full-section completion">\n'
            '  <div class="step-flow-panel">\n'
            '    <div class="section-label">End</div>\n'
            '    <div class="flow-timeline">\n'
            '      <div class="step-node theme-complete">\n'
            '        <div class="node-header">\n'
            '          <span class="node-title">Workflow End</span>\n'
            f'          {_badge("lock-release", "lock released")}\n'
            f"{fence_badge}"
            f"        </div>\n"
            f'        <div class="node-desc">'
            f"Completed at {_fmt_ts(completed.timestamp)}</div>\n"
            f"      </div>\n"
            f"    </div>\n"
            f"  </div>\n"
            f"</div>\n"
        )
    if failed:
        return (
            '<div class="full-section failed-wf">\n'
            '  <div class="step-flow-panel">\n'
            '    <div class="section-label">Failed</div>\n'
            '    <div class="flow-timeline">\n'
            '      <div class="step-node theme-fail">\n'
            '        <div class="node-header">\n'
            '          <span class="node-title">Workflow Failed</span>\n'
            f'          {_badge("lock-release", "lock released")}\n'
            f"        </div>\n"
            f'        <div class="node-desc">'
            f"Failed at {_fmt_ts(failed.timestamp)}</div>\n"
            f"      </div>\n"
            f"    </div>\n"
            f"  </div>\n"
            f"</div>\n"
        )

    cancelled = next(
        (e for e in wf_events if e.event_type == AuditEventType.WORKFLOW_CANCELLED), None
    )
    if cancelled:
        return (
            '<div class="full-section cancelled-wf">\n'
            '  <div class="step-flow-panel">\n'
            '    <div class="section-label">Cancelled</div>\n'
            '    <div class="flow-timeline">\n'
            '      <div class="step-node theme-neutral">\n'
            '        <div class="node-header">\n'
            '          <span class="node-title">Workflow Cancelled</span>\n'
            f'          {_badge("lock-release", "lock released")}\n'
            f"        </div>\n"
            f'        <div class="node-desc">'
            f"Cancelled at {_fmt_ts(cancelled.timestamp)}</div>\n"
            f"      </div>\n"
            f"    </div>\n"
            f"  </div>\n"
            f"</div>\n"
        )
    return ""


def _render_state_transitions(events: list[AuditEvent]) -> str:
    """Render a state transition table from actual event pairs."""
    # Collect unique transitions
    seen: set[tuple[str, str]] = set()
    rows: list[tuple[str, str, str]] = []

    for e in events:
        from_s = e.step_status_before or e.workflow_status_before
        to_s = e.step_status or e.workflow_status
        if from_s and to_s and from_s != to_s:
            pair = (from_s, to_s)
            if pair not in seen:
                seen.add(pair)
                trigger = e.event_type.value.replace("_", " ")
                rows.append((from_s, to_s, trigger))

    if not rows:
        return ""

    row_html = []
    for from_s, to_s, trigger in rows:
        from_cls = f"s-{from_s}" if from_s != "needs_review" else "s-review"
        to_cls = f"s-{to_s}" if to_s != "needs_review" else "s-review"
        row_html.append(
            f"      <tr>\n"
            f'        <td><span class="state-badge {from_cls}">{_esc(from_s)}</span></td>\n'
            f'        <td><span class="state-badge {to_cls}">{_esc(to_s)}</span></td>\n'
            f"        <td>{_esc(trigger)}</td>\n"
            f"      </tr>"
        )

    return (
        '<div class="full-section">\n'
        '  <div class="section-label">State Transitions (observed)</div>\n'
        '  <table class="state-table">\n'
        "    <thead>\n"
        "      <tr><th>From</th><th>To</th><th>Trigger</th></tr>\n"
        "    </thead>\n"
        "    <tbody>\n"
        + "\n".join(row_html)
        + "\n    </tbody>\n  </table>\n</div>\n"
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def generate_audit_report(
    events: list[AuditEvent],
    *,
    workflow: Workflow | None = None,
) -> str:
    """Generate a self-contained HTML execution report from audit events.

    Args:
        events: List of AuditEvent objects, ordered by sequence
                (as returned by MongoAuditLogger.get_events).
        workflow: Optional Workflow object.  When provided the dependency
                  graph shows *all* steps in the workflow (including those
                  not yet executed), giving a complete picture of the
                  workflow shape with progress overlay.

    Returns:
        Complete HTML document as a string.
    """
    if not events:
        return "<html><body><p>No audit events found.</p></body></html>"

    wf_name = events[0].workflow_name.replace("_", " ").title()

    wf_events, step_groups = _group_events(events)
    dep_map = _extract_dep_info(step_groups, workflow=workflow)

    # When the full workflow is available, provide the complete step list
    # so that unexecuted steps appear in the dependency graph.
    all_steps: list[tuple[int, str]] | None = None
    if workflow is not None:
        all_steps = [(i, s.name) for i, s in enumerate(workflow.steps)]

    tiers = _compute_tiers(step_groups, dep_map, all_steps=all_steps)
    step_states = _compute_step_states(step_groups, workflow=workflow)

    # Build sync/async mode map for dependency graph markers
    step_modes = _compute_step_modes(step_groups, workflow=workflow)

    wf_state = _workflow_final_state(wf_events)

    sections = [
        _render_header(wf_name),
        _render_summary(events, wf_name),
        _render_dependency_graph(dep_map, tiers, step_states, wf_state, step_modes=step_modes),
        '<div class="main-area">\n',
        _render_discovery(step_groups),
    ]

    lane_groups = _compute_lane_groups(dep_map, tiers)
    for group_type, group_data in lane_groups:
        if group_type == "single":
            idx, _name = group_data[0]
            if idx in step_groups:
                sections.append(_render_step_section(idx, step_groups[idx]))
        elif group_type == "parallel":
            group_parts = [
                '    <div class="parallel-group">\n',
                '      <div class="parallel-group-label">Parallel execution</div>\n',
            ]
            for idx, _name in group_data:
                if idx in step_groups:
                    group_parts.append(_render_step_section(idx, step_groups[idx]))
            group_parts.append("    </div>\n")
            sections.append("".join(group_parts))
        elif group_type == "lanes":
            group_parts = [
                '    <div class="parallel-group">\n',
                '      <div class="parallel-group-label">Parallel execution</div>\n',
            ]
            for lane in group_data:
                group_parts.append('      <div class="parallel-lane">\n')
                for sub_tier in lane:
                    if len(sub_tier) > 1:
                        group_parts.append('        <div class="parallel-lane-tier">\n')
                    for idx, _name in sub_tier:
                        if idx in step_groups:
                            group_parts.append(_render_step_section(idx, step_groups[idx]))
                    if len(sub_tier) > 1:
                        group_parts.append("        </div>\n")
                group_parts.append("      </div>\n")
            group_parts.append("    </div>\n")
            sections.append("".join(group_parts))

    sections.append(_render_completion(wf_events))
    sections.append(_render_state_transitions(events))
    sections.append("</div>\n")

    body = "\n".join(sections)

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f"<title>{_esc(wf_name)} &mdash; Execution Report</title>\n"
        f"<style>\n{CSS}</style>\n"
        "</head>\n<body>\n\n"
        + body
        + "\n</body>\n</html>\n"
    )
