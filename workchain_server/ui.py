"""Management dashboard UI for workchain server."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

ui_router = APIRouter()

DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SERVER_TITLE</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: #0a0e17; color: #e5e7eb;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    line-height: 1.6; padding: 2rem;
  }
  h1 { text-align: center; font-size: 2.2rem; color: #f9fafb; margin-bottom: 0.5rem; }
  .subtitle { text-align: center; font-size: 1.05rem; color: #9ca3af; margin-bottom: 2rem; }

  /* stats bar */
  .stats-bar {
    display: flex; gap: 12px; flex-wrap: wrap; justify-content: center;
    margin-bottom: 2rem;
  }
  .stat-card {
    background: #111827; border: 1px solid #1f2937; border-radius: 10px;
    padding: 1rem 1.5rem; min-width: 120px; text-align: center;
  }
  .stat-card .count { font-size: 2rem; font-weight: 700; }
  .stat-card .label {
    font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.06em;
    color: #6b7280; margin-top: 0.2rem;
  }
  .c-pending   { color: #9ca3af; }
  .c-running   { color: #34d399; }
  .c-completed { color: #34d399; }
  .c-failed    { color: #f87171; }
  .c-cancelled { color: #9ca3af; }
  .c-needs_review { color: #fbbf24; }

  /* workflow table */
  h2 { font-size: 1.4rem; color: #f9fafb; margin-bottom: 1rem; }
  table { width: 100%; border-collapse: collapse; font-size: 1rem; }
  th {
    text-align: left; padding: 0.6rem 0.85rem; color: #6b7280; font-weight: 600;
    text-transform: uppercase; font-size: 0.85rem; letter-spacing: 0.04em;
    border-bottom: 1px solid #1f2937;
  }
  td { padding: 0.6rem 0.85rem; border-bottom: 1px solid #111827; color: #d1d5db; }
  .badge {
    display: inline-block; font-size: 0.85rem; padding: 2px 10px;
    border-radius: 8px; font-weight: 600; font-family: monospace;
  }
  .b-pending   { background: rgba(107,114,128,0.15); color: #9ca3af; }
  .b-running   { background: rgba(52,211,153,0.1); color: #34d399; }
  .b-completed { background: rgba(52,211,153,0.15); color: #34d399; }
  .b-failed    { background: rgba(248,113,113,0.15); color: #f87171; }
  .b-needs_review { background: rgba(251,191,36,0.15); color: #fbbf24; }
  .b-cancelled { background: rgba(107,114,128,0.15); color: #9ca3af; }
  a { color: #818cf8; text-decoration: none; }
  a:hover { text-decoration: underline; }
  .empty { color: #4b5563; font-style: italic; padding: 1rem; text-align: center; }
  .cancel-btn {
    background: rgba(248,113,113,0.15); color: #f87171; border: none;
    padding: 2px 10px; border-radius: 8px; font-size: 0.85rem; font-weight: 600;
    font-family: monospace; cursor: pointer;
  }
  .cancel-btn:hover { background: rgba(248,113,113,0.3); }

  .toast {
    position: fixed; bottom: 1.5rem; right: 1.5rem; background: #065f46; color: #34d399;
    padding: 0.7rem 1.4rem; border-radius: 8px; font-size: 1rem; font-weight: 600;
    opacity: 0; transition: opacity 0.3s;
  }
  .toast.show { opacity: 1; }
</style>
</head>
<body>

<h1>SERVER_TITLE</h1>
<p class="subtitle">Workflow management &bull; Instance: <code>INSTANCE_ID</code></p>

<div class="stats-bar" id="stats-bar"></div>

<h2>Workflows</h2>
<table>
  <thead><tr><th>Name</th><th>Status</th><th>Progress</th><th>Created</th><th>Actions</th></tr></thead>
  <tbody id="wf-table"><tr><td colspan="5" class="empty">Loading...</td></tr></tbody>
</table>

<div class="toast" id="toast"></div>

<script>
const API = '/api/v1/workflows';

function el(tag, attrs, ...children) {
  const e = document.createElement(tag);
  if (attrs) Object.entries(attrs).forEach(([k, v]) => {
    if (k === 'className') e.className = v;
    else if (k === 'textContent') e.textContent = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  });
  children.forEach(c => { if (c) e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
  return e;
}

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 3000);
}

async function refreshStats() {
  try {
    const res = await fetch(API + '/stats');
    const data = await res.json();
    const bar = document.getElementById('stats-bar');
    bar.replaceChildren();
    const order = ['pending', 'running', 'completed', 'failed', 'needs_review', 'cancelled'];
    order.forEach(s => {
      const count = data[s] || 0;
      bar.appendChild(el('div', {className: 'stat-card'},
        el('div', {className: 'count c-' + s, textContent: String(count)}),
        el('div', {className: 'label', textContent: s.replace('_', ' ')}),
      ));
    });
  } catch (e) { /* retry on next interval */ }
}

async function refreshTable() {
  try {
    const res = await fetch(API);
    const workflows = await res.json();
    const tbody = document.getElementById('wf-table');
    tbody.replaceChildren();

    if (workflows.length === 0) {
      const tr = el('tr', null, el('td', {colspan: '5', className: 'empty', textContent: 'No workflows yet.'}));
      tbody.appendChild(tr);
      return;
    }

    const terminal = ['completed', 'failed', 'needs_review', 'cancelled'];
    workflows.forEach(wf => {
      const ts = wf.created_at ? new Date(wf.created_at).toLocaleTimeString() : '?';
      const safeId = encodeURIComponent(wf.id);

      const actions = el('td');
      if (wf.status !== 'pending') {
        actions.appendChild(el('a', {href: API + '/' + safeId + '/report', target: '_blank', textContent: 'Report'}));
      } else {
        actions.appendChild(el('span', {style: 'color:#4b5563', textContent: 'pending'}));
      }
      if (!terminal.includes(wf.status)) {
        const btn = el('button', {className: 'cancel-btn', textContent: 'Cancel', onclick: () => cancelWf(wf.id)});
        actions.appendChild(document.createTextNode(' '));
        actions.appendChild(btn);
      }

      tbody.appendChild(el('tr', null,
        el('td', {textContent: wf.name}),
        el('td', null, el('span', {className: 'badge b-' + wf.status, textContent: wf.status})),
        el('td', {textContent: wf.progress}),
        el('td', {textContent: ts}),
        actions,
      ));
    });
  } catch (e) { /* retry on next interval */ }
}

async function cancelWf(id) {
  try {
    const safeId = encodeURIComponent(id);
    const res = await fetch(API + '/' + safeId + '/cancel', { method: 'POST' });
    if (!res.ok) throw new Error(await res.text());
    showToast('Cancelled');
    refresh();
  } catch (e) { showToast('Error: ' + e.message); }
}

function refresh() { refreshStats(); refreshTable(); }
refresh();
setInterval(refresh, 3000);
</script>

</body>
</html>
"""


def create_ui_router(server_title: str, instance_id: str) -> APIRouter:
    """Create the UI router with server-specific values baked into the HTML."""
    router = APIRouter()

    page = DASHBOARD_HTML.replace("SERVER_TITLE", server_title).replace("INSTANCE_ID", instance_id)

    @router.get("/", response_class=HTMLResponse)
    async def dashboard():
        """Serve the management dashboard."""
        return HTMLResponse(page)

    return router
