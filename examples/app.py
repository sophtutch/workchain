"""FastAPI test harness for workchain example workflows.

Run:
    pip install -e ".[examples]"
    uvicorn examples.app:app --reload
    # Open http://localhost:8000
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from mongomock_motor import AsyncMongoMockClient

# Register all example step handlers via import side-effects
from examples.ci_cd_pipeline import steps as _ci_cd_steps  # noqa: F401
from examples.ci_cd_pipeline.workflow import build_workflow as build_ci_cd
from examples.customer_onboarding import steps as _onboard_steps  # noqa: F401
from examples.customer_onboarding.workflow import build_workflow as build_onboarding
from examples.data_pipeline_etl import steps as _etl_steps  # noqa: F401
from examples.data_pipeline_etl.workflow import build_workflow as build_etl
from examples.incident_response import steps as _incident_steps  # noqa: F401
from examples.incident_response.workflow import build_workflow as build_incident
from examples.infra_provisioning import steps as _infra_steps  # noqa: F401
from examples.infra_provisioning.workflow import build_workflow as build_infra
from workchain import MongoAuditLogger, MongoWorkflowStore, WorkflowEngine
from workchain.audit_report import generate_audit_report

# ---------------------------------------------------------------------------
# Example workflow definitions
# ---------------------------------------------------------------------------

EXAMPLES = {
    "customer_onboarding": {
        "title": "Customer Onboarding",
        "description": "Validate email, create account, provision resources (async), send welcome email",
        "steps": 4,
        "fields": [
            {"name": "email", "label": "Email", "default": "alice@example.com", "type": "text"},
        ],
        "builder": lambda params: build_onboarding(email=params["email"]),
    },
    "data_pipeline_etl": {
        "title": "Data Pipeline ETL",
        "description": "Extract, validate schema, transform, load to warehouse (async), update catalog",
        "steps": 5,
        "fields": [
            {"name": "source_uri", "label": "Source URI", "default": "postgres://src/orders", "type": "text"},
            {"name": "target_table", "label": "Target Table", "default": "analytics.orders", "type": "text"},
        ],
        "builder": lambda params: build_etl(
            source_uri=params["source_uri"],
            target_table=params["target_table"],
            columns=["id", "amount", "date", "customer_id"],
        ),
    },
    "ci_cd_pipeline": {
        "title": "CI/CD Pipeline",
        "description": "Lint, test (retry), build artifact (async), push registry, deploy staging (async), smoke tests",
        "steps": 6,
        "fields": [
            {"name": "repo", "label": "Repository", "default": "myorg/myapp", "type": "text"},
            {"name": "branch", "label": "Branch", "default": "main", "type": "text"},
        ],
        "builder": lambda params: build_ci_cd(repo=params["repo"], branch=params.get("branch", "main")),
    },
    "infra_provisioning": {
        "title": "Infrastructure Provisioning",
        "description": "VPC, provision DB (async), deploy app (async), DNS, TLS cert (async), health check",
        "steps": 6,
        "fields": [
            {"name": "domain", "label": "Domain", "default": "app.example.com", "type": "text"},
            {"name": "image", "label": "Container Image", "default": "myorg/app:latest", "type": "text"},
            {"name": "region", "label": "Region", "default": "us-east-1", "type": "text"},
        ],
        "builder": lambda params: build_infra(
            domain=params["domain"], image=params["image"], region=params.get("region", "us-east-1")
        ),
    },
    "incident_response": {
        "title": "Incident Response",
        "description": "Create ticket, page on-call (retry), diagnostics, remediate (async), verify, close",
        "steps": 6,
        "fields": [
            {"name": "service_name", "label": "Service", "default": "payment-api", "type": "text"},
            {"name": "severity", "label": "Severity", "default": "high", "type": "text"},
            {"name": "description", "label": "Description", "default": "Elevated error rate on checkout", "type": "text"},
        ],
        "builder": lambda params: build_incident(
            service_name=params["service_name"],
            severity=params["severity"],
            description=params["description"],
        ),
    },
}

# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

_client = AsyncMongoMockClient()
_db = _client["workchain_harness"]
store = MongoWorkflowStore(_db, lock_ttl_seconds=10)
audit_logger = MongoAuditLogger(_db)


@asynccontextmanager
async def lifespan(application: FastAPI):  # noqa: ARG001
    await store.ensure_indexes()
    await audit_logger.ensure_indexes()
    engine = WorkflowEngine(
        store,
        instance_id="harness-001",
        claim_interval=0.5,
        heartbeat_interval=2.0,
        sweep_interval=5.0,
        step_stuck_seconds=30.0,
        max_concurrent=10,
        audit_logger=audit_logger,
        context={"db": _db, "store": store, "audit_logger": audit_logger},
    )
    await engine.start()
    app.state.engine = engine
    yield
    await engine.stop()


app = FastAPI(title="workchain Test Harness", lifespan=lifespan)

# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------


@app.get("/api/workflows")
async def list_workflows():
    """List all workflows with their current status."""
    wf_list = await store.list_workflows()

    workflows = []
    for wf in wf_list:
        total_steps = len(wf.steps)
        completed_steps = sum(1 for s in wf.steps if s.status.value == "completed")
        workflows.append({
            "id": wf.id,
            "name": wf.name,
            "status": wf.status.value,
            "progress": f"{completed_steps}/{total_steps}",
            "current_step_index": wf.current_step_index,
            "total_steps": total_steps,
            "completed_steps": completed_steps,
            "created_at": str(wf.created_at),
        })
    return workflows


@app.get("/api/workflows/stats")
async def workflow_stats():
    """Return workflow counts grouped by status."""
    return await store.count_by_status()


@app.post("/workflows/{example}")
async def create_workflow(example: str, request: Request):
    """Create and submit a new workflow from an example template."""
    if example not in EXAMPLES:
        raise HTTPException(status_code=404, detail=f"Unknown example: {example}")

    body = await request.json()
    builder = EXAMPLES[example]["builder"]
    wf = builder(body)
    await store.insert(wf)

    return {"workflow_id": wf.id, "name": wf.name, "status": wf.status.value}


@app.get("/workflows/{workflow_id}")
async def get_workflow(workflow_id: str):
    """Get the current state of a workflow."""
    wf = await store.get(workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="Workflow not found")

    steps = []
    for s in wf.steps:
        step_info = {
            "name": s.name,
            "handler": s.handler,
            "status": s.status.value,
            "attempt": s.attempt,
            "is_async": s.is_async,
        }
        if s.result:
            step_info["result"] = s.result.model_dump(exclude_none=True)
        steps.append(step_info)

    return {
        "id": wf.id,
        "name": wf.name,
        "status": wf.status.value,
        "current_step_index": wf.current_step_index,
        "fence_token": wf.fence_token,
        "locked_by": wf.locked_by,
        "steps": steps,
    }


@app.post("/workflows/{workflow_id}/cancel")
async def cancel_workflow(workflow_id: str):
    """Cancel a running or pending workflow."""
    wf = await store.cancel_workflow(workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="Workflow not found or already terminal")
    return {"workflow_id": wf.id, "status": wf.status.value}


@app.get("/workflows/{workflow_id}/report", response_class=HTMLResponse)
async def get_workflow_report(workflow_id: str):
    """Generate an HTML audit report for a workflow."""
    wf = await store.get(workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="Workflow not found")

    events = await audit_logger.get_events(workflow_id)
    if not events:
        return HTMLResponse("<html><body><p>No audit events yet. The workflow may not have started.</p></body></html>")

    # Allow fire-and-forget audit writes to land
    await asyncio.sleep(0.1)
    events = await audit_logger.get_events(workflow_id)

    return HTMLResponse(generate_audit_report(events))


# ---------------------------------------------------------------------------
# Landing page
# ---------------------------------------------------------------------------

LANDING_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Workchain Test Harness</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: #0a0e17; color: #e5e7eb;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    line-height: 1.6; padding: 2rem;
  }
  h1 { text-align: center; font-size: 2.2rem; color: #f9fafb; margin-bottom: 0.5rem; }
  .subtitle { text-align: center; font-size: 1.05rem; color: #9ca3af; margin-bottom: 2rem; }
  .examples { display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 16px; margin-bottom: 2rem; }
  .card {
    background: #111827; border: 1px solid #1f2937; border-radius: 10px;
    padding: 1.5rem; display: flex; flex-direction: column;
  }
  .card h3 { font-size: 1.25rem; color: #c4b5fd; margin-bottom: 0.35rem; }
  .card .desc { font-size: 0.95rem; color: #6b7280; margin-bottom: 0.75rem; }
  .card .step-count { font-size: 0.85rem; color: #4b5563; margin-bottom: 0.75rem; }
  .field { margin-bottom: 0.6rem; }
  .field label { display: block; font-size: 0.85rem; color: #9ca3af; margin-bottom: 3px; text-transform: uppercase; letter-spacing: 0.04em; }
  .field input {
    width: 100%; background: #0d1117; border: 1px solid #1f2937; border-radius: 6px;
    color: #e5e7eb; padding: 0.5rem 0.75rem; font-size: 1rem;
    font-family: 'SF Mono', 'Fira Code', monospace;
  }
  .field input:focus { outline: none; border-color: #6366f1; }
  .run-btn {
    margin-top: auto; padding: 0.6rem 1.2rem; border: none; border-radius: 6px;
    background: #4f46e5; color: white; font-weight: 600; font-size: 1rem;
    cursor: pointer; text-transform: uppercase; letter-spacing: 0.04em;
  }
  .run-btn:hover { background: #4338ca; }
  .run-btn:disabled { opacity: 0.5; cursor: not-allowed; }

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
  a { color: #818cf8; text-decoration: none; font-size: 1rem; }
  a:hover { text-decoration: underline; }
  .empty { color: #4b5563; font-style: italic; padding: 1rem; text-align: center; font-size: 1rem; }
  .toast {
    position: fixed; bottom: 1.5rem; right: 1.5rem; background: #065f46; color: #34d399;
    padding: 0.7rem 1.4rem; border-radius: 8px; font-size: 1rem; font-weight: 600;
    opacity: 0; transition: opacity 0.3s;
  }
  .toast.show { opacity: 1; }
</style>
</head>
<body>

<h1>Workchain Test Harness</h1>
<p class="subtitle">Create and run example workflows &bull; View audit execution reports</p>

<div class="examples" id="examples"></div>

<h2>Workflows</h2>
<table>
  <thead><tr><th>Name</th><th>Status</th><th>Progress</th><th>Created</th><th>Report</th></tr></thead>
  <tbody id="wf-table"><tr><td colspan="5" class="empty">No workflows yet. Create one above.</td></tr></tbody>
</table>

<div class="toast" id="toast"></div>

<script>
const EXAMPLES = EXAMPLES_JSON;

function renderExamples() {
  const container = document.getElementById('examples');
  container.innerHTML = '';
  for (const [key, ex] of Object.entries(EXAMPLES)) {
    const card = document.createElement('div');
    card.className = 'card';
    let fieldsHtml = '';
    for (const f of ex.fields) {
      fieldsHtml += `<div class="field"><label>${f.label}</label><input name="${f.name}" value="${f.default}" data-example="${key}"></div>`;
    }
    card.innerHTML = `
      <h3>${ex.title}</h3>
      <div class="desc">${ex.description}</div>
      <div class="step-count">${ex.steps} steps</div>
      ${fieldsHtml}
      <button class="run-btn" onclick="runWorkflow('${key}', this)">Run</button>
    `;
    container.appendChild(card);
  }
}

async function runWorkflow(example, btn) {
  btn.disabled = true;
  btn.textContent = 'Starting...';
  const card = btn.closest('.card');
  const inputs = card.querySelectorAll('input');
  const params = {};
  inputs.forEach(i => { params[i.name] = i.value; });

  try {
    const res = await fetch(`/workflows/${example}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(params),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    showToast(`Created: ${data.name} (${data.workflow_id.slice(0, 8)}...)`);
    refreshTable();
  } catch (e) {
    showToast('Error: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Run';
  }
}

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 3000);
}

async function refreshTable() {
  try {
    const res = await fetch('/api/workflows');
    const workflows = await res.json();
    const tbody = document.getElementById('wf-table');

    if (workflows.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" class="empty">No workflows yet. Create one above.</td></tr>';
      return;
    }

    tbody.innerHTML = workflows.map(wf => {
      const badgeCls = 'b-' + wf.status;
      const ts = wf.created_at ? new Date(wf.created_at).toLocaleTimeString() : '?';
      const reportLink = wf.status !== 'pending'
        ? `<a href="/workflows/${wf.id}/report" target="_blank">View Report</a>`
        : '<span style="color:#4b5563">pending</span>';
      const terminal = ['completed', 'failed', 'needs_review', 'cancelled'];
      const cancelBtn = terminal.includes(wf.status)
        ? ''
        : `<button class="badge b-failed" style="cursor:pointer;border:none;" onclick="cancelWorkflow('${wf.id}')">Cancel</button>`;
      return `<tr>
        <td>${wf.name}</td>
        <td><span class="badge ${badgeCls}">${wf.status}</span></td>
        <td>${wf.progress}</td>
        <td>${ts}</td>
        <td>${reportLink} ${cancelBtn}</td>
      </tr>`;
    }).join('');
  } catch (e) {
    // Silently retry on next interval
  }
}

async function cancelWorkflow(wfId) {
  try {
    const res = await fetch(`/workflows/${wfId}/cancel`, { method: 'POST' });
    if (!res.ok) throw new Error(await res.text());
    showToast('Cancelled');
    refreshTable();
  } catch (e) {
    showToast('Error: ' + e.message);
  }
}

renderExamples();
refreshTable();
setInterval(refreshTable, 2000);
</script>

</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def landing_page():
    """Serve the landing page with example workflow cards."""
    import json

    examples_json = {}
    for key, ex in EXAMPLES.items():
        examples_json[key] = {
            "title": ex["title"],
            "description": ex["description"],
            "steps": ex["steps"],
            "fields": ex["fields"],
        }

    page = LANDING_HTML.replace("EXAMPLES_JSON", json.dumps(examples_json))
    return HTMLResponse(page)
