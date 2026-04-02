"""Generate self-contained HTML execution reports from audit events.

Usage:
    events = await audit_logger.get_events(workflow_id)
    html = generate_audit_report(events)
"""

from __future__ import annotations

import html
from collections import defaultdict
from typing import TYPE_CHECKING

from workchain.audit import AuditEvent, AuditEventType

if TYPE_CHECKING:
    from datetime import datetime

# ---------------------------------------------------------------------------
# CSS — same visual style as examples/generate_diagrams.py
# ---------------------------------------------------------------------------

CSS = """\
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: #0a0e17; color: #e5e7eb;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
    line-height: 1.6; padding: 2rem 1.5rem;
  }

  /* header */
  .page-header { text-align: center; margin-bottom: 2rem; }
  .page-header h1 { font-size: 1.75rem; font-weight: 700; color: #f9fafb; letter-spacing: -0.02em; }
  .page-header .subtitle { font-size: 0.82rem; color: #9ca3af; margin-top: 0.35rem; letter-spacing: 0.02em; }

  /* summary banner */
  .summary-banner {
    background: linear-gradient(135deg, #1e1b4b 0%, #0f172a 100%);
    border: 1px solid #312e81; border-radius: 10px;
    padding: 1.1rem 1.5rem; margin-bottom: 2rem;
    display: flex; align-items: center; gap: 1.5rem; flex-wrap: wrap;
  }
  .summary-banner .wf-name { font-weight: 700; font-size: 1rem; color: #c4b5fd; }
  .summary-banner .wf-name code {
    background: #312e81; padding: 0.15em 0.5em; border-radius: 4px;
    font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace; font-size: 0.92em;
  }
  .summary-stats { display: flex; gap: 1rem; flex-wrap: wrap; margin-left: auto; }
  .stat {
    font-size: 0.72rem; font-weight: 600; padding: 0.2em 0.7em;
    border-radius: 999px; text-transform: uppercase; letter-spacing: 0.04em;
  }
  .stat.completed { background: rgba(52,211,153,0.15); color: #34d399; }
  .stat.failed    { background: rgba(248,113,113,0.15); color: #f87171; }
  .stat.running   { background: rgba(165,180,252,0.15); color: #a5b4fc; }
  .stat.review    { background: rgba(251,191,36,0.15); color: #fbbf24; }
  .stat.neutral   { background: rgba(107,114,128,0.15); color: #9ca3af; }

  /* step flow panel */
  .step-flow-panel { background: #111827; border: 1px solid #1f2937; border-radius: 10px; padding: 16px; }

  /* main area */
  .main-area { display: flex; flex-direction: column; }

  /* step section: 3-column grid */
  .step-section {
    display: grid; grid-template-columns: 1fr 140px 360px; gap: 20px;
    align-items: stretch; padding: 20px 0; border-top: 1px solid #1f2937;
  }
  .step-section:first-child { border-top: none; }
  .step-section > .step-flow-panel { height: 100%; box-sizing: border-box; }
  .step-doc { display: flex; flex-direction: column; }
  .step-doc .panel { flex: 1; margin-bottom: 0; }

  /* transition column */
  .step-transitions { display: flex; flex-direction: column; gap: 6px; padding: 4px 0; }
  .tx-block {
    border-left: 3px solid; border-radius: 4px; padding: 5px 8px;
    flex: 1; display: flex; flex-direction: column; justify-content: center;
  }
  .tx-label {
    font-size: 8.5px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.04em; opacity: 0.6; margin-bottom: 2px;
  }
  .tx-value { font-size: 10px; font-family: monospace; line-height: 1.3; }
  .tx-green  { border-color: #34d399; background: rgba(52,211,153,0.07); color: #34d399; }
  .tx-indigo { border-color: #a5b4fc; background: rgba(165,180,252,0.07); color: #a5b4fc; }
  .tx-amber  { border-color: #fbbf24; background: rgba(251,191,36,0.07); color: #fbbf24; }
  .tx-red    { border-color: #f87171; background: rgba(248,113,113,0.07); color: #f87171; }
  .tx-gray   { border-color: #9ca3af; background: rgba(156,163,175,0.07); color: #9ca3af; }
  .tx-purple { border-color: #c084fc; background: rgba(192,132,252,0.07); color: #c084fc; }

  /* full-width section */
  .full-section { padding: 20px 0; border-top: 1px solid #1f2937; }

  /* section label */
  .section-label {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 1.5px; color: #4b5563; margin-bottom: 12px;
    padding-bottom: 8px; border-bottom: 1px solid #1f2937;
  }

  /* panels */
  .panel {
    background: #111827; border: 1px solid #1f2937; border-radius: 10px;
    padding: 1.25rem; margin-bottom: 1.25rem;
  }
  .panel-title {
    font-size: 0.72rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.08em; color: #6b7280; margin-bottom: 0.85rem;
  }
  .doc-label {
    font-size: 0.68rem; font-weight: 600; color: #6b7280;
    text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 0.4rem;
  }

  /* flow timeline */
  .flow-timeline { position: relative; padding-left: 28px; }
  .flow-timeline::before {
    content: ''; position: absolute; left: 13px; top: 0; bottom: 0;
    width: 2px; background: #1f2937;
  }

  /* step nodes */
  .step-node {
    position: relative; margin-bottom: 0.75rem; padding: 0.85rem 1rem;
    background: #111827; border: 1px solid #1f2937; border-radius: 8px;
    animation: fadeIn 0.4s ease-out both;
  }
  .step-node::before {
    content: ''; position: absolute; left: -19px; top: 1.1rem;
    width: 10px; height: 10px; border-radius: 50%;
    border: 2px solid #1f2937; background: #0a0e17;
  }
  .step-node.theme-submit  { border-left: 3px solid #34d399; }
  .step-node.theme-submit::before  { border-color: #34d399; background: #065f46; }
  .step-node.theme-sync    { border-left: 3px solid #a5b4fc; }
  .step-node.theme-sync::before    { border-color: #a5b4fc; background: #312e81; }
  .step-node.theme-async   { border-left: 3px solid #fbbf24; }
  .step-node.theme-async::before   { border-color: #fbbf24; background: #451a03; }
  .step-node.theme-complete { border-left: 3px solid #34d399; background: #064e3b22; }
  .step-node.theme-complete::before { border-color: #34d399; background: #34d399; }
  .step-node.theme-fail    { border-left: 3px solid #f87171; }
  .step-node.theme-fail::before    { border-color: #f87171; background: #7f1d1d; }
  .step-node.theme-engine  { border-left: 3px solid #6366f1; }
  .step-node.theme-engine::before  { border-color: #6366f1; background: #312e81; }

  .node-header {
    display: flex; align-items: center; gap: 0.5rem;
    margin-bottom: 0.3rem; flex-wrap: wrap;
  }
  .node-title { font-weight: 700; font-size: 0.88rem; color: #f3f4f6; }
  .node-desc { font-size: 0.76rem; color: #9ca3af; }

  /* badges */
  .badge {
    font-size: 0.65rem; font-weight: 700; padding: 0.15em 0.55em;
    border-radius: 999px; text-transform: uppercase; letter-spacing: 0.04em;
    display: inline-flex; align-items: center; gap: 0.25em;
  }
  .badge.engine-action { background: #6366f1; color: #e0e7ff; }
  .badge.lock-claim    { background: #065f46; color: #34d399; border: 1px solid #34d399; }
  .badge.lock-release  { background: #7f1d1d; color: #f87171; border: 1px solid #f87171; }
  .badge.status-badge  { background: #1f2937; color: #9ca3af; }
  .badge.fence-badge   { background: #312e81; color: #a5b4fc; }

  code, .mono {
    font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
    font-size: 0.82em;
  }

  /* retry sub-track */
  .retry-track { margin: 0.5rem 0 0.25rem 1rem; padding-left: 1rem; border-left: 2px dashed #f87171; }
  .retry-item { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.35rem; font-size: 0.76rem; }
  .retry-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
  .retry-dot.fail { background: #f87171; }
  .retry-dot.ok   { background: #34d399; }

  /* poll sub-track */
  .poll-track { margin: 0.5rem 0 0.25rem 1rem; padding-left: 1rem; border-left: 2px dashed #fbbf24; }
  .poll-item { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.4rem; font-size: 0.76rem; }
  .poll-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .poll-dot.pending { background: #4b5563; }
  .poll-dot.done    { background: #34d399; }
  .poll-progress {
    background: #1f2937; border-radius: 4px; height: 6px; width: 80px;
    overflow: hidden; display: inline-block; vertical-align: middle; margin-left: 0.35rem;
  }
  .poll-progress-fill { height: 100%; border-radius: 4px; background: #fbbf24; }
  .poll-instance { font-size: 0.65rem; color: #6b7280; font-style: italic; }

  /* mongodb doc */
  .mongo-doc {
    background: #0d1117; border: 1px solid #1f2937; border-radius: 6px;
    padding: 0.85rem;
    font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
    font-size: 0.7rem; line-height: 1.7; overflow-x: auto; color: #c9d1d9;
  }
  .mongo-doc .key { color: #79c0ff; }
  .mongo-doc .str { color: #a5d6ff; }
  .mongo-doc .num { color: #ffa657; }
  .mongo-doc .kw  { color: #ff7b72; }

  /* state transitions table */
  .state-table { width: 100%; border-collapse: collapse; font-size: 11px; margin-top: 12px; }
  .state-table th {
    text-align: left; font-size: 10px; text-transform: uppercase;
    letter-spacing: 0.5px; color: #4b5563; padding: 6px 10px;
    border-bottom: 1px solid #1f2937; font-weight: 600;
  }
  .state-table td { padding: 7px 10px; border-bottom: 1px solid #111827; color: #6b7280; vertical-align: top; }
  .state-table tr:last-child td { border-bottom: none; }
  .state-badge {
    display: inline-block; font-size: 9px; padding: 1px 7px;
    border-radius: 8px; font-weight: 600; font-family: monospace;
  }
  .s-pending   { background: rgba(107,114,128,0.15); color: #9ca3af; border: 1px solid rgba(107,114,128,0.2); }
  .s-running   { background: rgba(52,211,153,0.1);   color: #34d399; border: 1px solid rgba(52,211,153,0.2); }
  .s-blocked   { background: rgba(251,191,36,0.1);   color: #fbbf24; border: 1px solid rgba(251,191,36,0.2); }
  .s-failed    { background: rgba(248,113,113,0.1);  color: #f87171; border: 1px solid rgba(248,113,113,0.2); }
  .s-completed { background: rgba(52,211,153,0.1);   color: #34d399; border: 1px solid rgba(52,211,153,0.2); }
  .s-review    { background: rgba(251,191,36,0.1);   color: #fbbf24; border: 1px solid rgba(251,191,36,0.2); }

  /* fade-in */
  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(8px); }
    to   { opacity: 1; transform: translateY(0); }
  }
"""

for _i in range(1, 21):
    CSS += f"  .step-node:nth-child({_i})  {{ animation-delay: {_i * 0.05:.2f}s; }}\n"


# ---------------------------------------------------------------------------
# HTML helpers (shared patterns from generate_diagrams.py)
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


def _mongo_doc(fields: dict) -> str:
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


def _fail_node(title: str, desc: str) -> str:
    return (
        '          <div class="step-node theme-fail">\n'
        '            <div class="node-header">\n'
        f'              <span class="node-title">{_esc(title)}</span>\n'
        f'              {_badge("status-badge", "FAILED")}\n'
        f"            </div>\n"
        f'            <div class="node-desc">{_esc(desc)}</div>\n'
        f"          </div>\n"
    )


# ---------------------------------------------------------------------------
# Event grouping
# ---------------------------------------------------------------------------

_WORKFLOW_EVENTS = frozenset({
    AuditEventType.WORKFLOW_CREATED,
    AuditEventType.WORKFLOW_CLAIMED,
    AuditEventType.WORKFLOW_COMPLETED,
    AuditEventType.WORKFLOW_FAILED,
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

    # Determine final status
    final_status = "unknown"
    status_cls = "neutral"
    for e in reversed(events):
        if e.event_type == AuditEventType.WORKFLOW_COMPLETED:
            final_status = "completed"
            status_cls = "completed"
            break
        if e.event_type == AuditEventType.WORKFLOW_FAILED:
            final_status = "failed"
            status_cls = "failed"
            break
        if e.event_type == AuditEventType.RECOVERY_NEEDS_REVIEW:
            final_status = "needs_review"
            status_cls = "review"
            break

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


def _render_discovery(wf_events: list[AuditEvent]) -> str:
    """Render the discovery/claim section from WORKFLOW_CLAIMED event."""
    claim = next((e for e in wf_events if e.event_type == AuditEventType.WORKFLOW_CLAIMED), None)
    if claim is None:
        return ""

    flow = (
        '      <div class="step-flow-panel">\n'
        '        <div class="section-label">Discovery</div>\n'
        '        <div class="flow-timeline">\n'
        '          <div class="step-node theme-engine">\n'
        '            <div class="node-header">\n'
        f'              <span class="node-title">try_claim()</span>\n'
        f'              {_badge("lock-claim", "lock acquired")}\n'
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
        + _tx("green", "Locks", "1 claim")
        + _tx("indigo", "Fence Token", f"fence_token &rarr; {claim.fence_token}")
    )
    tx_col = f'      <div class="step-transitions">\n{txs}      </div>\n'

    doc_fields = {
        "fence_token": claim.fence_token,
        "locked_by": claim.instance_id,
        "status": "running",
    }
    doc = (
        '      <div class="step-doc">\n'
        '        <div class="panel">\n'
        '          <div class="panel-title">Start Workflow</div>\n'
        '          <div class="doc-label">workflows collection</div>\n'
        f'          <div class="mongo-doc">{_mongo_doc(doc_fields)}</div>\n'
        "        </div>\n"
        "      </div>\n"
    )

    return f'    <div class="step-section">\n{flow}{tx_col}{doc}    </div>\n'


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

    # Classify events
    submitted = [e for e in step_events if e.event_type == AuditEventType.STEP_SUBMITTED]
    running = [e for e in step_events if e.event_type == AuditEventType.STEP_RUNNING]
    completed = [e for e in step_events if e.event_type == AuditEventType.STEP_COMPLETED]
    failed = [e for e in step_events if e.event_type == AuditEventType.STEP_FAILED]
    blocked = [e for e in step_events if e.event_type == AuditEventType.STEP_BLOCKED]
    polls = [e for e in step_events if e.event_type == AuditEventType.POLL_CHECKED]
    poll_timeout = [e for e in step_events if e.event_type == AuditEventType.POLL_TIMEOUT]
    poll_max = [e for e in step_events if e.event_type == AuditEventType.POLL_MAX_EXCEEDED]
    lock_released = [e for e in step_events if e.event_type == AuditEventType.LOCK_RELEASED]
    recovery = [e for e in step_events if e.event_type.value.startswith("recovery_")]
    advanced = [e for e in step_events if e.event_type == AuditEventType.STEP_ADVANCED]

    # --- Flow panel ---
    mode = "async" if is_async else "sync"
    label = f"Step {step_num} &mdash; {_esc(step_name)} ({mode})"
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
                # If this isn't the last attempt, or it's the last and step failed, it's a fail dot
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
    nodes += [_fail_node("Poll Timeout", pt.error or "Poll timeout") for pt in poll_timeout]
    nodes += [_fail_node("Max Polls Exceeded", pm.error or "Max polls exceeded") for pm in poll_max]

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
        nodes.append(
            '          <div class="step-node theme-fail">\n'
            '            <div class="node-header">\n'
            f'              <span class="node-title">Failed</span>\n'
            f'              {_badge("status-badge", "FAILED")}\n'
            f"            </div>\n"
            f'            <div class="node-desc">{_esc(e.error or "Step failed")}</div>\n'
            f"          </div>\n"
        )

    flow_panel = (
        f'      <div class="step-flow-panel">\n'
        f'        <div class="section-label">{label}</div>\n'
        f'        <div class="flow-timeline">\n'
        + "".join(nodes)
        + "        </div>\n      </div>\n"
    )

    # --- Transition column ---
    txs = []

    # Lock claimed (if reclaim after async)
    claim_events = [e for e in step_events if e.event_type == AuditEventType.WORKFLOW_CLAIMED]
    n_claims = len(claim_events)
    if claim_events:
        txs.append(_tx("green", "Locks", f"{n_claims} {'claim' if n_claims == 1 else 'claims'}"))
        txs.append(_tx("indigo", "Fence Token", f"fence_token &rarr; {claim_events[0].fence_token}"))

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

    # Count all lock releases for this step
    n_releases = len(lock_released)
    if blocked:
        n_releases += 1  # the BLOCKED event itself implies a release

    if polls:
        txs.append(_tx("amber", "Polls", f"{len(polls)} polls"))
        if len(polls) > 1:
            txs.append(_tx("indigo", "Fence Token", f"fence_token +{len(polls)}"))

    if completed:
        txs.append(_tx("green", "Step Status", "&rarr; completed"))
    elif failed:
        txs.append(_tx("red", "Step Status", "&rarr; failed"))

    if advanced:
        txs.append(_tx("purple", "Step Index", f"idx &rarr; {step_num}"))

    if n_releases > 0:
        txs.append(_tx("red", "Locks", f"{n_releases} released"))

    tx_col = f'      <div class="step-transitions">\n{"".join(txs)}      </div>\n'

    # --- Doc diff panel ---
    doc_fields: dict = {}
    # Use the last meaningful event's data
    final = completed[0] if completed else (failed[0] if failed else None)
    if final and final.result_summary:
        doc_fields["current_step_index"] = step_num
        if final.fence_token:
            doc_fields["fence_token"] = final.fence_token
        doc_fields[f"steps[{idx}]"] = {
            "name": step_name,
            "result": final.result_summary,
            "status": "completed" if completed else "failed",
        }
    elif final and final.error:
        doc_fields[f"steps[{idx}]"] = {
            "name": step_name,
            "status": "failed",
            "error": _truncate(final.error),
        }

    doc = (
        '      <div class="step-doc">\n'
        '        <div class="panel">\n'
        f'          <div class="panel-title">After Step {step_num} &mdash; {_esc(step_name)}</div>\n'
        f'          <div class="doc-label">changes</div>\n'
        f'          <div class="mongo-doc">{_mongo_doc(doc_fields)}</div>\n'
        "        </div>\n"
        "      </div>\n"
    )

    return f'    <div class="step-section">\n{flow_panel}{tx_col}{doc}    </div>\n'


def _render_completion(wf_events: list[AuditEvent]) -> str:
    """Render the workflow completion or failure section."""
    completed = next(
        (e for e in wf_events if e.event_type == AuditEventType.WORKFLOW_COMPLETED), None
    )
    failed = next(
        (e for e in wf_events if e.event_type == AuditEventType.WORKFLOW_FAILED), None
    )

    if completed:
        return (
            '<div class="full-section">\n'
            '  <div class="step-flow-panel">\n'
            '    <div class="section-label">Complete</div>\n'
            '    <div class="flow-timeline">\n'
            '      <div class="step-node theme-complete">\n'
            '        <div class="node-header">\n'
            '          <span class="node-title">Workflow Complete</span>\n'
            f'          {_badge("lock-release", "lock released")}\n'
            f'          {_badge("fence-badge", f"fence_token: {completed.fence_token}")}\n'
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
            '<div class="full-section">\n'
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


def generate_audit_report(events: list[AuditEvent]) -> str:
    """Generate a self-contained HTML execution report from audit events.

    Args:
        events: List of AuditEvent objects, ordered by sequence
                (as returned by MongoAuditLogger.get_events).

    Returns:
        Complete HTML document as a string.
    """
    if not events:
        return "<html><body><p>No audit events found.</p></body></html>"

    wf_name = events[0].workflow_name

    wf_events, step_groups = _group_events(events)

    sections = [
        _render_header(wf_name),
        _render_summary(events, wf_name),
        '<div class="main-area">\n',
        _render_discovery(wf_events),
    ]

    sections += [_render_step_section(idx, step_groups[idx]) for idx in sorted(step_groups.keys())]

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
