#!/usr/bin/env python3
"""Generate self-contained flow_diagram.html for each workchain example.

Usage:
    python examples/generate_diagrams.py

Each generated file is standalone (no external CSS/JS dependencies).
"""

from __future__ import annotations

import html
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class RetryScenario:
    max_attempts: int
    wait: float
    multiplier: float
    # Which 1-based attempt numbers fail (the rest succeed).
    # e.g. [1, 2] means attempts 1 and 2 fail, attempt 3 succeeds.
    fail_attempts: list[int] = field(default_factory=list)


@dataclass
class PollScenario:
    interval: float
    backoff: float
    timeout: float
    num_polls: int
    # Percentage strings per poll, e.g. ["33%", "66%", "100%"]
    percentages: list[str] = field(default_factory=list)
    # Instance names per poll (cycles through workflow.instances if not set)
    instances: list[str] = field(default_factory=list)


@dataclass
class StepData:
    name: str
    handler: str
    is_async: bool
    is_final: bool = False
    idempotent: bool = False

    retry: RetryScenario | None = None
    poll: PollScenario | None = None

    # Result fields for the mongo-doc diff  {key: value}
    result_fields: dict = field(default_factory=dict)

    # Human-readable config description for the section label
    config_desc: str = ""

    # Handler description for the flow panel node
    handler_desc: str = ""

    # Computed at generation time
    needs_reclaim: bool = False


@dataclass
class WorkflowData:
    name: str
    title: str
    subtitle: str
    steps: list[StepData]
    instances: list[str] = field(default_factory=lambda: ["inst_a1", "inst_b2", "inst_c3"])
    fast_sweep_interval: str = "1s"
    heartbeat_interval: str = "TTL/3"
    slow_sweep_interval: str = "30s"


# ---------------------------------------------------------------------------
# CSS (unified from customer_onboarding + incident_response best-of)
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

  /* example banner */
  .example-banner {
    background: linear-gradient(135deg, #1e1b4b 0%, #0f172a 100%);
    border: 1px solid #312e81; border-radius: 10px;
    padding: 1.1rem 1.5rem; margin-bottom: 2rem;
    display: flex; align-items: center; gap: 1.5rem; flex-wrap: wrap;
  }
  .example-banner .wf-name { font-weight: 700; font-size: 1rem; color: #c4b5fd; }
  .example-banner .wf-name code {
    background: #312e81; padding: 0.15em 0.5em; border-radius: 4px;
    font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace; font-size: 0.92em;
  }
  .step-chips { display: flex; gap: 0.5rem; flex-wrap: wrap; }
  .step-chip {
    font-size: 0.72rem; font-weight: 600; padding: 0.2em 0.7em;
    border-radius: 999px; text-transform: uppercase; letter-spacing: 0.04em;
  }
  .step-chip.sync  { background: #312e81; color: #a5b4fc; }
  .step-chip.async { background: #451a03; color: #fbbf24; }

  /* info bar */
  .info-bar { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 24px; }
  @media (max-width: 900px) { .info-bar { grid-template-columns: repeat(2, 1fr); } }
  .info-card {
    background: #111827; border: 1px solid #1f2937; border-left-width: 3px;
    border-radius: 10px; padding: 14px 16px;
  }
  .info-card.loop-fast  { border-left-color: #34d399; }
  .info-card.loop-heart { border-left-color: #fbbf24; }
  .info-card.loop-slow  { border-left-color: #f87171; }
  .info-card.fence-token { border-left-color: #6366f1; }
  .info-label { font-size: 12px; font-weight: 700; margin-bottom: 6px; }
  .info-card.loop-fast   .info-label { color: #34d399; }
  .info-card.loop-heart  .info-label { color: #fbbf24; }
  .info-card.loop-slow   .info-label { color: #f87171; }
  .info-card.fence-token .info-label { color: #a5b4fc; }
  .info-desc { font-size: 10.5px; color: #6b7280; line-height: 1.5; }
  .info-interval { font-size: 10px; color: #4b5563; margin-top: 6px; font-family: monospace; }

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
  .retry-wait { font-size: 0.68rem; color: #6b7280; font-style: italic; }

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
  .poll-progress-fill { height: 100%; border-radius: 4px; background: #fbbf24; transition: width 0.3s; }
  .poll-instance { font-size: 0.65rem; color: #6b7280; font-style: italic; }

  /* crash callout */
  .crash-callout { border: 2px dashed #f87171; border-radius: 8px; padding: 0.85rem 1rem; margin: 0.5rem 0; background: #111827; }
  .crash-callout .crash-title { font-weight: 700; font-size: 0.82rem; color: #f87171; margin-bottom: 0.4rem; }
  .crash-tree { font-size: 0.74rem; color: #d1d5db; padding-left: 0.5rem; }
  .crash-tree .branch { margin-bottom: 0.25rem; padding-left: 1rem; position: relative; }
  .crash-tree .branch::before { content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 1px; background: #374151; }
  .crash-tree .branch::after { content: ''; position: absolute; left: 0; top: 0.6em; width: 0.6rem; height: 1px; background: #374151; }
  .crash-tree .arrow { color: #6b7280; }
  .crash-tree .yes   { color: #34d399; }
  .crash-tree .no    { color: #f87171; }

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

  /* state transitions — incident_response style */
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
# Add animation-delay for step-node children (up to 20)
for _i in range(1, 21):
    CSS += f"  .step-node:nth-child({_i})  {{ animation-delay: {_i * 0.05:.2f}s; }}\n"


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _esc(v: str) -> str:
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
    if isinstance(v, (int, float)):
        return f'<span class="num">{v}</span>'
    if isinstance(v, str):
        return f'<span class="str">"{_esc(v)}"</span>'
    if isinstance(v, list):
        if not v:
            return "[]"
        inner = [f"{prefix}  {_mongo_val(item, indent + 1)}" for item in v]
        return "[\n" + ",\n".join(inner) + f"\n{prefix}]"
    return _esc(str(v))


def _mongo_doc(fields: dict) -> str:
    """Render a dict as a full mongo-doc <pre> block."""
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


# ---------------------------------------------------------------------------
# Section generators
# ---------------------------------------------------------------------------


def _render_header(wf: WorkflowData) -> str:
    return (
        f'<div class="page-header">\n'
        f"  <h1>{_esc(wf.title)} &mdash; Flow Diagram</h1>\n"
        f'  <div class="subtitle">{_esc(wf.subtitle)}</div>\n'
        f"</div>\n"
    )


def _render_banner(wf: WorkflowData) -> str:
    chips = []
    for i, s in enumerate(wf.steps, 1):
        cls = "async" if s.is_async else "sync"
        chips.append(f'    <span class="step-chip {cls}">{i} &nbsp;{_esc(s.name)}</span>')
    return (
        f'<div class="example-banner">\n'
        f'  <div class="wf-name">Workflow: <code>{_esc(wf.name)}</code></div>\n'
        f'  <div class="step-chips">\n'
        + "\n".join(chips)
        + "\n  </div>\n</div>\n"
    )


def _render_info_bar(wf: WorkflowData) -> str:
    return textwrap.dedent(f"""\
    <div class="info-bar">
      <div class="info-card loop-fast">
        <div class="info-label">Fast Sweep</div>
        <div class="info-desc">Discovers workflows with <code style="color:#34d399;">next_poll_at &le; now</code> or status&nbsp;=&nbsp;<code style="color:#34d399;">pending</code>. Drives the claim-poll-release cycle for async steps.</div>
        <div class="info-interval">every {_esc(wf.fast_sweep_interval)} &rarr; find_claimable()</div>
      </div>
      <div class="info-card loop-heart">
        <div class="info-label">Heartbeat</div>
        <div class="info-desc">Extends lock TTL on all workflows claimed by this instance. If the process dies, the lock expires after TTL&nbsp;seconds.</div>
        <div class="info-interval">every {_esc(wf.heartbeat_interval)} &rarr; heartbeat()</div>
      </div>
      <div class="info-card loop-slow">
        <div class="info-label">Slow Sweep</div>
        <div class="info-desc">Detects anomalies: steps stuck in <code style="color:#f87171;">SUBMITTED</code> or <code style="color:#f87171;">RUNNING</code> without a lock; completed steps with un-advanced index; stale locks past TTL.</div>
        <div class="info-interval">every {_esc(wf.slow_sweep_interval)} &rarr; find_anomalies()</div>
      </div>
      <div class="info-card fence-token">
        <div class="info-label">Fence Token</div>
        <div class="info-desc">Incremented on every <code style="color:#a5b4fc;">try_claim()</code>. Every write includes the token in its query filter. If the token doesn&rsquo;t match, the write is silently rejected &mdash; the instance lost its lock.</div>
        <div class="info-interval" style="line-height:1.8; margin-top:8px;">
          Instance A claims &rarr; fence=<span style="color:#fbbf24">N</span><br>
          Lock expires, B claims &rarr; fence=<span style="color:#fbbf24">N+1</span><br>
          A tries to write &rarr; <span style="color:#f87171">rejected</span>
        </div>
      </div>
    </div>
    """)


def _render_discovery(wf: WorkflowData, fence: int) -> str:
    inst = wf.instances[0]
    # Flow panel
    flow = textwrap.dedent(f"""\
      <div class="step-flow-panel">
        <div class="section-label">Discovery</div>
        <div class="flow-timeline">
          <div class="step-node theme-engine">
            <div class="node-header">
              <span class="node-title">Fast Sweep</span>
              <span class="badge engine-action">engine</span>
            </div>
            <div class="node-desc">
              Sweep finds workflow with <code>status: "pending"</code>. Calls <code>try_claim()</code>.
            </div>
          </div>
          <div class="step-node theme-submit">
            <div class="node-header">
              <span class="node-title">try_claim()</span>
              <span class="badge lock-claim">lock acquired</span>
              <span class="badge fence-badge">fence_token &rarr; {fence}</span>
            </div>
            <div class="node-desc">
              Atomic <code>findOneAndUpdate</code> &mdash; sets <code>locked_by: "{_esc(inst)}"</code>, <code>status: "running"</code>, increments <code>fence_token</code>. Only one instance wins.
            </div>
          </div>
        </div>
      </div>""")

    # Transitions
    tx = (
        '      <div class="step-transitions">\n'
        + _tx("purple", "Workflow", "pending &rarr; running")
        + _tx("green", "Lock", "claimed")
        + _tx("indigo", "Fence Token", f"fence_token &rarr; {fence}")
        + "      </div>"
    )

    # Doc
    doc_fields = {
        "_id": f"wf_{wf.name[:6]}...",
        "fence_token": fence,
        "locked_by": inst,
        "name": wf.name,
        "status": "running",
        "steps": [],
    }
    doc = (
        '      <div class="step-doc">\n'
        f'        <div class="panel">\n'
        f'          <div class="panel-title">Start Workflow</div>\n'
        f'          <div class="doc-label">workflows collection</div>\n'
        f'          <div class="mongo-doc">{_mongo_doc(doc_fields)}</div>\n'
        f"        </div>\n"
        f"      </div>"
    )

    return f'    <div class="step-section">\n{flow}\n{tx}\n{doc}\n    </div>\n'


def _render_step_section(
    wf: WorkflowData,
    step: StepData,
    idx: int,
    fence_before: int,
    fence_after: int,
    instance: str,
    prev_instance: str,
) -> str:
    step_num = idx + 1
    parts = []

    # --- Flow panel ---
    label_parts = [f"Step {step_num} &mdash; {_esc(step.name)}"]
    if step.config_desc:
        label_parts.append(f"({_esc(step.config_desc)})")
    section_label = " ".join(label_parts)

    flow_nodes = []

    # Write-Ahead node
    if step.needs_reclaim:
        flow_nodes.append(textwrap.dedent(f"""\
          <div class="step-node theme-engine">
            <div class="node-header">
              <span class="node-title">Fast Sweep &rarr; try_claim()</span>
              <span class="badge lock-claim">lock acquired</span>
              <span class="badge fence-badge">fence &rarr; {fence_before + 1 if step.needs_reclaim else fence_before}</span>
            </div>
            <div class="node-desc">Sweep discovers workflow at <code>current_step_index: {idx}</code>, claims lock.</div>
          </div>"""))

    flow_nodes.append(textwrap.dedent(f"""\
          <div class="step-node theme-submit">
            <div class="node-header">
              <span class="node-title">Write-Ahead</span>
              <span class="badge status-badge">SUBMITTED</span>
            </div>
            <div class="node-desc">
              <code>steps[{idx}].status &rarr; "submitted"</code> persisted to MongoDB <em>before</em> handler executes. Crash-safe boundary.
            </div>
          </div>"""))

    # Handler node
    if step.is_async:
        handler_theme = "theme-async"
        handler_badge = '<span class="badge engine-action">async submit</span>'
        handler_desc = step.handler_desc or "Handler starts async job. Returns result. Step &rarr; <code>BLOCKED</code>."
    else:
        handler_theme = "theme-sync"
        handler_badge = '<span class="badge engine-action">sync exec</span>'
        handler_desc = step.handler_desc or "Handler executes synchronously. Returns result."

    handler_node = f"""\
          <div class="step-node {handler_theme}">
            <div class="node-header">
              <span class="node-title">{_esc(step.handler)}()</span>
              {handler_badge}
            </div>
            <div class="node-desc">{handler_desc}</div>"""

    # Retry sub-track
    if step.retry:
        handler_node += '\n            <div class="retry-track">'
        wait = step.retry.wait
        for attempt in range(1, step.retry.max_attempts + 1):
            if attempt in step.retry.fail_attempts:
                handler_node += (
                    f'\n              <div class="retry-item">'
                    f'<span class="retry-dot fail"></span>'
                    f'<span style="color:#f87171;">Attempt {attempt}</span>'
                    f'<span style="color:#6b7280;"> &mdash; failed</span></div>'
                )
                if attempt < step.retry.max_attempts:
                    handler_node += (
                        f'\n              <div class="retry-item">'
                        f'<span class="retry-wait">wait {wait:.1f}s</span></div>'
                    )
                    wait *= step.retry.multiplier
            elif attempt == max(step.retry.fail_attempts, default=0) + 1:
                handler_node += (
                    f'\n              <div class="retry-item">'
                    f'<span class="retry-dot ok"></span>'
                    f'<span style="color:#34d399;">Attempt {attempt}</span>'
                    f'<span style="color:#6b7280;"> &mdash; success</span></div>'
                )
                break
        handler_node += "\n            </div>"

    handler_node += "\n          </div>"
    flow_nodes.append(handler_node)

    # Async: BLOCKED + poll track
    if step.is_async and step.poll:
        poll = step.poll
        flow_nodes.append(textwrap.dedent("""\
          <div class="step-node theme-async">
            <div class="node-header">
              <span class="node-title">BLOCKED</span>
              <span class="badge lock-release">lock released</span>
              <span class="badge status-badge">next_poll_at set</span>
            </div>
            <div class="node-desc">
              Lock released. Workflow available for any instance to claim on next sweep.
            </div>
          </div>"""))

        # Poll cycle node
        poll_instances = poll.instances or wf.instances
        poll_items = []
        poll_fence = fence_before + 1 if step.needs_reclaim else fence_before
        poll_fence += 1  # initial async submit claim
        for pi in range(poll.num_polls):
            pinst = poll_instances[pi % len(poll_instances)]
            poll_fence += 1
            is_last = pi == poll.num_polls - 1
            dot_cls = "done" if is_last else "pending"
            pct = poll.percentages[pi] if pi < len(poll.percentages) else "?"
            pbar_color = " background:#34d399;" if is_last and pct == "100%" else ""

            poll_items.append(
                f'              <div class="poll-item"{' style="margin-top:0.3rem;"' if pi > 0 else ""}>'
                f'<span class="poll-dot {dot_cls}"></span>'
                f"<span><strong>Poll {pi + 1}</strong></span>"
                f'<span class="badge lock-claim" style="font-size:0.6rem;">claim</span>'
                f'<span class="badge fence-badge" style="font-size:0.6rem;">fence &rarr; {poll_fence}</span>'
                f'<span class="poll-instance">{_esc(pinst)}</span></div>'
            )
            poll_items.append(
                f'              <div class="poll-item" style="padding-left:1rem;">'
                f'<span style="color:#9ca3af;">completeness_check &rarr;</span>'
                f'<span style="color:{"#34d399" if is_last else "#fbbf24"};">{_esc(pct)}</span>'
                f'<div class="poll-progress"><div class="poll-progress-fill" style="width:{_esc(pct)};{pbar_color}"></div></div>'
                + ("" if is_last else '<span class="badge lock-release" style="font-size:0.6rem;">release</span>')
                + "</div>"
            )

        poll_html = "\n".join(poll_items)
        flow_nodes.append(
            f'          <div class="step-node theme-engine">\n'
            f'            <div class="node-header">\n'
            f'              <span class="node-title">Claim-Poll-Release Cycle</span>\n'
            f'              <span class="badge engine-action">engine loop</span>\n'
            f"            </div>\n"
            f'            <div class="node-desc">Fast sweep rediscovers workflow when '
            f"<code>next_poll_at &le; now</code>. Different instances claim each poll.</div>\n"
            f'            <div class="poll-track">\n{poll_html}\n'
            f"            </div>\n"
            f"          </div>"
        )

    # Advance node
    advance_badges = f'<span class="badge status-badge">COMPLETED</span> <span class="badge fence-badge">fence_token: {fence_after}</span>'
    if step.is_async:
        advance_badges += ' <span class="badge lock-release">lock released</span>'
    if step.is_final:
        advance_badges += ' <span class="badge lock-release">lock released</span>'

    advance_desc = f'<code>steps[{idx}].status &rarr; "completed"</code>, <code>current_step_index &rarr; {step_num}</code>.'
    if step.is_final:
        advance_desc += ' <code>workflow.status &rarr; "completed"</code>.'

    flow_nodes.append(
        f'          <div class="step-node theme-complete">\n'
        f'            <div class="node-header">\n'
        f'              <span class="node-title">{"Workflow Complete" if step.is_final else "Advance"}</span>\n'
        f"              {advance_badges}\n"
        f"            </div>\n"
        f'            <div class="node-desc">{advance_desc}</div>\n'
        f"          </div>"
    )

    flow_panel = (
        f'      <div class="step-flow-panel">\n'
        f"        <div class=\"section-label\">{section_label}</div>\n"
        f'        <div class="flow-timeline">\n'
        + "\n".join(flow_nodes)
        + "\n        </div>\n      </div>"
    )

    # --- Transition column ---
    txs = []
    if step.needs_reclaim:
        reclaim_fence = fence_before + 1
        txs.append(_tx("green", "Lock", "claimed"))
        txs.append(_tx("indigo", "Fence Token", f"fence_token &rarr; {reclaim_fence}"))

    txs.append(_tx("indigo", "Step Status", "&rarr; submitted"))

    if step.is_async:
        txs.append(_tx("amber", "Handler", "async submit"))
        txs.append(_tx("amber", "Step Status", "&rarr; blocked"))
        txs.append(_tx("red", "Lock", "released"))
        if step.poll:
            txs.append(_tx("amber", "Polls", f"{step.poll.num_polls} polls"))
            txs.append(_tx("indigo", "Fence Token", f"fence_token +{step.poll.num_polls}"))
    else:
        txs.append(_tx("indigo", "Handler", "sync exec"))
        if step.retry and step.retry.fail_attempts:
            n_fails = len(step.retry.fail_attempts)
            txs.append(_tx("red", "Retries", f"{n_fails} {'retry' if n_fails == 1 else 'retries'}"))

    txs.append(_tx("green", "Step Status", "&rarr; completed"))
    txs.append(_tx("purple", "Step Index", f"idx &rarr; {step_num}"))

    if step.is_final:
        txs.append(_tx("purple", "Workflow", "&rarr; completed"))
        txs.append(_tx("red", "Lock", "released"))

    tx_col = '      <div class="step-transitions">\n' + "".join(txs) + "      </div>"

    # --- Doc panel (diff only) ---
    diff_fields: dict = {}
    diff_fields["current_step_index"] = step_num

    if fence_after != fence_before:
        diff_fields["fence_token"] = fence_after

    if step.needs_reclaim or step.is_async:
        diff_fields["locked_by"] = None if step.is_final else instance

    if step.is_final:
        diff_fields["locked_by"] = None
        diff_fields["status"] = "completed"

    step_entry: dict = {
        "handler": step.handler,
        "name": step.name,
    }
    if step.is_async and step.poll:
        step_entry["is_async"] = True
        step_entry["poll_count"] = step.poll.num_polls
    if step.retry and step.retry.fail_attempts:
        step_entry["attempt"] = max(step.retry.fail_attempts) + 1
    step_entry["result"] = step.result_fields
    step_entry["status"] = "completed"

    diff_fields[f"steps[{idx}]"] = step_entry

    doc_col = (
        f'      <div class="step-doc">\n'
        f'        <div class="panel">\n'
        f'          <div class="panel-title">After Step {step_num} &mdash; {_esc(step.name)}</div>\n'
        f'          <div class="doc-label">workflows collection</div>\n'
        f'          <div class="mongo-doc">{_mongo_doc(diff_fields)}</div>\n'
        f"        </div>\n"
        f"      </div>"
    )

    return f'    <div class="step-section">\n{flow_panel}\n{tx_col}\n{doc_col}\n    </div>\n'


def _render_complete(wf: WorkflowData, final_fence: int) -> str:
    n_steps = len(wf.steps)
    return textwrap.dedent(f"""\
    <div class="full-section">
      <div class="step-flow-panel">
        <div class="section-label">Complete</div>
        <div class="flow-timeline">
          <div class="step-node theme-complete">
            <div class="node-header">
              <span class="node-title">Workflow Complete</span>
              <span class="badge lock-release">lock released</span>
              <span class="badge fence-badge">fence_token: {final_fence}</span>
            </div>
            <div class="node-desc">
              <code>workflow.status &rarr; "completed"</code>. All {n_steps} steps finished. Lock released. No further sweeps will pick this workflow up.
            </div>
          </div>
        </div>
      </div>
    </div>
    """)


def _render_retry_failure() -> str:
    return textwrap.dedent("""\
    <div class="full-section">
      <div class="step-flow-panel">
        <div class="section-label">Retry Failure Path</div>
        <div class="flow-timeline">
          <div class="step-node theme-fail">
            <div class="node-header">
              <span class="node-title">All Retries Exhausted</span>
              <span class="badge lock-release">lock released</span>
            </div>
            <div class="node-desc">
              When all <code>max_attempts</code> fail, the step is marked <code>FAILED</code>.
            </div>
            <div class="retry-track">
              <div class="retry-item">
                <span class="retry-dot fail"></span>
                <span style="color:#f87171;">Attempt N / N</span>
                <span style="color:#6b7280;">&mdash; final attempt fails</span>
              </div>
              <div class="retry-item" style="margin-top:0.25rem; font-size:0.74rem;">
                <span style="color:#6b7280;">&darr;</span>
              </div>
              <div class="retry-item">
                <span style="color:#f87171;"><code>steps[i].status &rarr; "failed"</code></span>
              </div>
              <div class="retry-item">
                <span style="color:#f87171;"><code>workflow.status &rarr; "failed"</code></span>
              </div>
              <div class="retry-item">
                <span style="color:#6b7280;">Lock released. Error + traceback persisted to <code>steps[i].error</code>.</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
    """)


def _render_crash_recovery() -> str:
    return textwrap.dedent("""\
    <div class="full-section">
      <div class="step-flow-panel">
        <div class="section-label">Crash Recovery Path</div>
        <div class="flow-timeline">
          <div class="step-node theme-fail">
            <div class="node-header">
              <span class="node-title">Crash During Step Execution</span>
            </div>
            <div class="node-desc" style="margin-bottom:0.5rem;">
              Process dies mid-step. Lock TTL expires. Slow sweep detects orphaned step in <code>SUBMITTED</code> or <code>RUNNING</code> state without a live lock.
            </div>
            <div class="crash-callout">
              <div class="crash-title">Recovery Decision Tree</div>
              <div class="crash-tree">
                <div><strong>1. Has <code>verify_completion</code> hook?</strong></div>
                <div class="branch">
                  <span class="yes">YES</span> <span class="arrow">&rarr;</span> Call it. If returns True &rarr; mark <code>COMPLETED</code>, advance.
                </div>
                <div class="branch">
                  <span class="no">NO</span> <span class="arrow">&darr;</span>
                </div>
                <div style="margin-top:0.35rem;"><strong>2. Is async step with <code>completeness_check</code>?</strong></div>
                <div class="branch">
                  <span class="yes">YES</span> <span class="arrow">&rarr;</span> Run <code>completeness_check</code>. If complete &rarr; <code>COMPLETED</code>. Else &rarr; set <code>BLOCKED</code>, resume polling.
                </div>
                <div class="branch">
                  <span class="no">NO</span> <span class="arrow">&darr;</span>
                </div>
                <div style="margin-top:0.35rem;"><strong>3. Is step handler idempotent?</strong></div>
                <div class="branch">
                  <span class="yes">YES</span> <span class="arrow">&rarr;</span> Re-execute handler from scratch. Safe because idempotent.
                </div>
                <div class="branch">
                  <span class="no">NO</span> <span class="arrow">&rarr;</span> Mark step <code>NEEDS_REVIEW</code>. Human intervention required.
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
    """)


def _render_state_transitions() -> str:
    rows = [
        ("pending", "running", "<code>try_claim()</code> &mdash; atomic findOneAndUpdate, increments fence_token"),
        ("running", "running", "Step completes, <code>current_step_index</code> advances, next step begins"),
        ("running", "blocked", "Async step submitted, lock released, <code>next_poll_at</code> persisted"),
        ("blocked", "running", "<code>completeness_check</code> returns complete, lock kept, step advances"),
        ("running", "completed", "Final step result persisted, all steps done, lock released"),
        ("running", "failed", "All retry attempts exhausted, exception persisted, lock released"),
        ("running", "review", "Crash recovery: step not idempotent and cannot be safely re-run"),
    ]
    row_html = []
    for from_s, to_s, trigger in rows:
        badge_cls_map = {"review": "s-review"}
        from_cls = badge_cls_map.get(from_s, f"s-{from_s}")
        to_cls = badge_cls_map.get(to_s, f"s-{to_s}")
        to_label = "needs_review" if to_s == "review" else to_s
        row_html.append(
            f"      <tr>\n"
            f'        <td><span class="state-badge {from_cls}">{from_s}</span></td>\n'
            f'        <td><span class="state-badge {to_cls}">{to_label}</span></td>\n'
            f"        <td>{trigger}</td>\n"
            f"      </tr>"
        )
    return (
        '<div class="full-section">\n'
        '  <div class="section-label">State Transitions</div>\n'
        '  <table class="state-table">\n'
        "    <thead>\n"
        "      <tr><th>From</th><th>To</th><th>Trigger</th></tr>\n"
        "    </thead>\n"
        "    <tbody>\n"
        + "\n".join(row_html)
        + "\n    </tbody>\n  </table>\n</div>\n"
    )


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------


def _compute_fence_schedule(wf: WorkflowData) -> list[tuple[int, int]]:
    """Return (fence_before, fence_after) for each step.

    fence_before = fence value at start of step execution.
    fence_after  = fence value after step completes.
    """
    fence = 1  # after Discovery claim
    schedule = []
    for step in wf.steps:
        fb = fence
        if step.needs_reclaim:
            fence += 1  # try_claim
        if step.is_async and step.poll:
            fence += 1  # initial submit claim
            fence += step.poll.num_polls  # each poll claim
        schedule.append((fb, fence))
    return schedule


def _mark_reclaims(wf: WorkflowData) -> None:
    """Set needs_reclaim on steps that follow an async step."""
    for i, step in enumerate(wf.steps):
        if i > 0 and wf.steps[i - 1].is_async:
            step.needs_reclaim = True


def generate(wf: WorkflowData) -> str:
    _mark_reclaims(wf)
    fence_schedule = _compute_fence_schedule(wf)
    final_fence = fence_schedule[-1][1] if fence_schedule else 1

    # Determine which instance is active for each step
    inst_idx = 0
    step_instances: list[tuple[str, str]] = []  # (active_instance, prev_instance)
    for i, step in enumerate(wf.steps):
        prev = wf.instances[inst_idx % len(wf.instances)]
        if step.needs_reclaim:
            inst_idx += 1
        if step.is_async and step.poll:
            # After polls, last poll instance becomes active
            poll_insts = step.poll.instances or wf.instances
            inst_idx = wf.instances.index(poll_insts[(step.poll.num_polls - 1) % len(poll_insts)]) if poll_insts[0] in wf.instances else inst_idx + step.poll.num_polls
        active = wf.instances[inst_idx % len(wf.instances)]
        step_instances.append((active, prev))

    sections = [
        _render_header(wf),
        _render_banner(wf),
        _render_info_bar(wf),
        '<div class="main-area">\n',
        _render_discovery(wf, fence=1),
    ]

    for i, step in enumerate(wf.steps):
        fb, fa = fence_schedule[i]
        active_inst, prev_inst = step_instances[i]
        sections.append(
            _render_step_section(wf, step, i, fb, fa, active_inst, prev_inst)
        )

    sections.append(_render_complete(wf, final_fence))
    sections.append(_render_retry_failure())
    sections.append(_render_crash_recovery())
    sections.append(_render_state_transitions())
    sections.append("</div>\n")  # close main-area

    body = "\n".join(sections)

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f"<title>{_esc(wf.title)} &mdash; Flow Diagram</title>\n"
        f"<style>\n{CSS}</style>\n"
        "</head>\n<body>\n\n"
        + body
        + "\n</body>\n</html>\n"
    )


# ---------------------------------------------------------------------------
# Example workflow definitions
# ---------------------------------------------------------------------------

WORKFLOWS = [
    WorkflowData(
        name="customer_onboarding",
        title="Customer Onboarding",
        subtitle="Sweep-only discovery \u2022 Claim-poll-release for async steps \u2022 MongoDB as sole state store \u2022 Multi-instance safe",
        instances=["host1-a3f2", "host2-b7e1", "host3-c4d9"],
        steps=[
            StepData(
                name="validate_email",
                handler="validate_email",
                is_async=False,
                config_desc="sync, retry:3",
                handler_desc='Handler validates email format. Returns <code>{"validated": true, "email": "user@co.com"}</code>.',
                result_fields={"validated": True, "email": "user@co.com"},
            ),
            StepData(
                name="create_account",
                handler="create_account",
                is_async=False,
                config_desc="sync, retry:5, exponential backoff",
                handler_desc="Creates user account in the database. Exponential backoff with <code>wait_multiplier: 2.0</code>.",
                retry=RetryScenario(max_attempts=5, wait=1.0, multiplier=2.0, fail_attempts=[1, 2]),
                result_fields={"user_id": "acc_7x9"},
            ),
            StepData(
                name="provision_resources",
                handler="provision_resources",
                is_async=True,
                config_desc="async, poll\u00d73, interval=2.0s, backoff=1.5\u00d7, timeout=60s",
                handler_desc='Handler starts provisioning job. Returns <code>{"job_id": "prov_42"}</code>. Step &rarr; <code>BLOCKED</code>.',
                poll=PollScenario(
                    interval=2.0, backoff=1.5, timeout=60.0, num_polls=3,
                    percentages=["33%", "66%", "100%"],
                    instances=["host1-a3f2", "host2-b7e1", "host3-c4d9"],
                ),
                result_fields={"job_id": "prov_42"},
            ),
            StepData(
                name="send_welcome_email",
                handler="send_welcome_email",
                is_async=False,
                is_final=True,
                config_desc="sync",
                handler_desc='Sends welcome email via SMTP. Returns <code>{"email_sent": true}</code>.',
                result_fields={"email_sent": True},
            ),
        ],
    ),
    WorkflowData(
        name="data_pipeline_etl",
        title="Data Pipeline ETL",
        subtitle="Extract \u2022 Validate \u2022 Transform \u2022 Load \u2022 Catalog \u2022 Async warehouse load with polling",
        instances=["inst_a1", "inst_b2", "inst_c3"],
        steps=[
            StepData(
                name="extract_from_source",
                handler="extract_from_source",
                is_async=False,
                idempotent=True,
                config_desc="sync",
                handler_desc='Extracts records from source database. Returns <code>{"records_extracted": 3000}</code>.',
                result_fields={"records_extracted": 3000, "source_uri": "postgres://src/orders"},
            ),
            StepData(
                name="validate_schema",
                handler="validate_schema",
                is_async=False,
                idempotent=True,
                config_desc="sync",
                handler_desc='Validates extracted data against expected schema. Returns <code>{"valid": true}</code>.',
                result_fields={"valid": True, "column_count": 12},
            ),
            StepData(
                name="transform_records",
                handler="transform_records",
                is_async=False,
                idempotent=True,
                config_desc="sync",
                handler_desc="Applies transformations: type casting, null handling, derived columns.",
                result_fields={"records_transformed": 2940, "dropped": 60},
            ),
            StepData(
                name="load_to_warehouse",
                handler="load_to_warehouse",
                is_async=True,
                config_desc="async, poll\u00d73",
                handler_desc='Submits bulk load job to warehouse. Returns <code>{"load_id": "load_8f3a"}</code>.',
                poll=PollScenario(
                    interval=2.0, backoff=1.5, timeout=60.0, num_polls=3,
                    percentages=["33%", "66%", "100%"],
                ),
                result_fields={"load_id": "load_8f3a", "records_loaded": 2940},
            ),
            StepData(
                name="update_catalog",
                handler="update_catalog",
                is_async=False,
                is_final=True,
                idempotent=True,
                config_desc="sync",
                handler_desc='Updates data catalog with new table metadata. Returns <code>{"updated": true}</code>.',
                result_fields={"catalog_entry_id": "cat_9e2b", "updated": True},
            ),
        ],
    ),
    WorkflowData(
        name="ci_cd_pipeline",
        title="CI/CD Pipeline",
        subtitle="Lint \u2022 Test \u2022 Build \u2022 Push \u2022 Deploy \u2022 Smoke Test \u2022 Two async build/deploy phases",
        instances=["inst_a1", "inst_b2", "inst_c3"],
        steps=[
            StepData(
                name="lint_code",
                handler="lint_code",
                is_async=False,
                idempotent=True,
                config_desc="sync",
                handler_desc='Runs linters against the source tree. Returns <code>{"lint_passed": true, "warnings": 2}</code>.',
                result_fields={"lint_passed": True, "warnings": 2},
            ),
            StepData(
                name="run_tests",
                handler="run_tests",
                is_async=False,
                config_desc="sync, retry:3",
                handler_desc="Runs test suite with coverage. Retries on flaky failures.",
                retry=RetryScenario(max_attempts=3, wait=1.0, multiplier=2.0, fail_attempts=[1]),
                result_fields={"tests_passed": 142, "tests_failed": 0, "coverage": 87.3},
            ),
            StepData(
                name="build_artifact",
                handler="build_artifact",
                is_async=True,
                config_desc="async, poll\u00d73",
                handler_desc='Submits container build to CI. Returns <code>{"build_id": "bld_f4a1"}</code>.',
                poll=PollScenario(
                    interval=3.0, backoff=1.0, timeout=120.0, num_polls=3,
                    percentages=["33%", "66%", "100%"],
                ),
                result_fields={"build_id": "bld_f4a1", "artifact_url": "ghcr.io/org/app:sha-abc123"},
            ),
            StepData(
                name="push_to_registry",
                handler="push_to_registry",
                is_async=False,
                idempotent=True,
                config_desc="sync",
                handler_desc='Pushes built artifact to container registry. Returns <code>{"image_tag": "v2.1.0"}</code>.',
                result_fields={"image_tag": "v2.1.0", "registry_url": "ghcr.io/org/app:v2.1.0"},
            ),
            StepData(
                name="deploy_staging",
                handler="deploy_staging",
                is_async=True,
                config_desc="async, poll\u00d72",
                handler_desc='Deploys to staging environment. Returns <code>{"deployment_id": "dep_7b2c"}</code>.',
                poll=PollScenario(
                    interval=5.0, backoff=1.0, timeout=300.0, num_polls=2,
                    percentages=["50%", "100%"],
                ),
                result_fields={"deployment_id": "dep_7b2c", "environment": "staging"},
            ),
            StepData(
                name="run_smoke_tests",
                handler="run_smoke_tests",
                is_async=False,
                is_final=True,
                idempotent=True,
                config_desc="sync, final step",
                handler_desc="Runs smoke tests against staging deployment. Verifies health endpoints and critical user flows.",
                result_fields={"smoke_passed": True, "endpoints_checked": 8},
            ),
        ],
    ),
    WorkflowData(
        name="infra_provisioning",
        title="Infrastructure Provisioning",
        subtitle="VPC \u2022 Database \u2022 App Deploy \u2022 DNS \u2022 TLS \u2022 Health Check \u2022 Three async provisioning phases",
        instances=["inst_a1", "inst_b2", "inst_c3"],
        steps=[
            StepData(
                name="create_vpc",
                handler="create_vpc",
                is_async=False,
                idempotent=True,
                config_desc="sync",
                handler_desc='Creates VPC and subnets. Returns <code>{"vpc_id": "vpc-abc123"}</code>.',
                result_fields={"vpc_id": "vpc-abc123", "subnet_ids": ["subnet-1a", "subnet-2b"]},
            ),
            StepData(
                name="provision_database",
                handler="provision_database",
                is_async=True,
                config_desc="async, poll\u00d73",
                handler_desc='Provisions RDS database instance. Returns <code>{"db_instance_id": "db-xyz"}</code>.',
                poll=PollScenario(
                    interval=5.0, backoff=1.5, timeout=600.0, num_polls=3,
                    percentages=["25%", "60%", "100%"],
                ),
                result_fields={"db_instance_id": "db-xyz", "endpoint": "db-xyz.rds.amazonaws.com", "port": 5432},
            ),
            StepData(
                name="deploy_application",
                handler="deploy_application",
                is_async=True,
                config_desc="async, poll\u00d72",
                handler_desc='Deploys application containers. Returns <code>{"deployment_id": "dep-k8s-01"}</code>.',
                poll=PollScenario(
                    interval=3.0, backoff=1.0, timeout=300.0, num_polls=2,
                    percentages=["50%", "100%"],
                ),
                result_fields={"deployment_id": "dep-k8s-01", "replicas_ready": 2},
            ),
            StepData(
                name="configure_dns",
                handler="configure_dns",
                is_async=False,
                idempotent=True,
                config_desc="sync",
                handler_desc='Creates DNS records for the application. Returns <code>{"fqdn": "app.example.com"}</code>.',
                result_fields={"record_id": "rec-dns-01", "fqdn": "app.example.com"},
            ),
            StepData(
                name="issue_tls_cert",
                handler="issue_tls_cert",
                is_async=True,
                config_desc="async, poll\u00d72",
                handler_desc='Requests TLS certificate from CA. Returns <code>{"certificate_arn": "arn:aws:acm:..."}</code>.',
                poll=PollScenario(
                    interval=10.0, backoff=1.0, timeout=900.0, num_polls=2,
                    percentages=["50%", "100%"],
                ),
                result_fields={"certificate_arn": "arn:aws:acm:us-east-1:cert/abc", "valid_until": "2027-04-01"},
            ),
            StepData(
                name="health_check",
                handler="health_check",
                is_async=False,
                is_final=True,
                idempotent=True,
                config_desc="sync, final step",
                handler_desc="Verifies application health endpoint returns 200. Confirms end-to-end provisioning.",
                result_fields={"status_code": 200, "response_time_ms": 45.2, "healthy": True},
            ),
        ],
    ),
    WorkflowData(
        name="incident_response",
        title="Incident Response",
        subtitle="Ticket \u2022 Page \u2022 Diagnose \u2022 Remediate \u2022 Verify \u2022 Close \u2022 Async remediation with polling",
        instances=["inst_a1", "inst_b2", "inst_c3"],
        steps=[
            StepData(
                name="create_ticket",
                handler="create_ticket",
                is_async=False,
                config_desc="sync",
                handler_desc='Creates incident ticket in tracking system. Returns <code>{"ticket_id": "INC-4521"}</code>.',
                result_fields={"ticket_id": "INC-4521", "created_at": "2026-04-01T10:00:00Z"},
            ),
            StepData(
                name="page_oncall",
                handler="page_oncall",
                is_async=False,
                config_desc="sync, retry:3",
                handler_desc="Pages on-call engineer via PagerDuty. Retries on transient failures.",
                retry=RetryScenario(max_attempts=3, wait=1.0, multiplier=2.0, fail_attempts=[1]),
                result_fields={"paged_user": "oncall-eng-7", "acknowledged": True},
            ),
            StepData(
                name="gather_diagnostics",
                handler="gather_diagnostics",
                is_async=False,
                idempotent=True,
                config_desc="sync",
                handler_desc="Collects logs, metrics, and traces from affected services.",
                result_fields={"logs_collected": 1250, "metrics_snapshot": {"error_rate": 0.12, "p99_ms": 890}},
            ),
            StepData(
                name="apply_remediation",
                handler="apply_remediation",
                is_async=True,
                config_desc="async, poll\u00d73",
                handler_desc='Applies remediation action (e.g. rollback, scale-up). Returns <code>{"remediation_id": "rem-01"}</code>.',
                poll=PollScenario(
                    interval=2.0, backoff=1.5, timeout=120.0, num_polls=3,
                    percentages=["33%", "66%", "100%"],
                ),
                result_fields={"remediation_id": "rem-01", "action_taken": "rollback_to_v2.0.3"},
            ),
            StepData(
                name="verify_resolution",
                handler="verify_resolution",
                is_async=False,
                idempotent=True,
                config_desc="sync",
                handler_desc="Verifies that the remediation resolved the incident. Checks service health.",
                result_fields={"service_healthy": True, "latency_ms": 42.5},
            ),
            StepData(
                name="close_ticket",
                handler="close_ticket",
                is_async=False,
                is_final=True,
                idempotent=True,
                config_desc="sync, final step",
                handler_desc="Closes the incident ticket with resolution summary.",
                result_fields={"closed": True, "resolution_time_minutes": 18.5},
            ),
        ],
    ),
]


def main() -> None:
    examples_dir = Path(__file__).parent
    for wf in WORKFLOWS:
        out_dir = examples_dir / wf.name
        out_dir.mkdir(exist_ok=True)
        out_file = out_dir / "flow_diagram.html"
        content = generate(wf)
        out_file.write_text(content, encoding="utf-8")
        print(f"  OK {out_file.relative_to(examples_dir.parent)}")

    print(f"\nGenerated {len(WORKFLOWS)} flow diagrams.")


if __name__ == "__main__":
    main()
