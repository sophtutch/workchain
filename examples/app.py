"""FastAPI test harness for workchain example workflows.

Run:
    pip install -e ".[examples]"
    uvicorn examples.app:app --reload
    # Open http://localhost:8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
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
from examples.media_processing import steps as _media_steps  # noqa: F401
from examples.media_processing.workflow import build_workflow as build_media
from examples.ml_training import steps as _ml_steps  # noqa: F401
from examples.ml_training.workflow import build_workflow as build_ml
from examples.order_fulfillment import steps as _order_steps  # noqa: F401
from examples.order_fulfillment.workflow import build_workflow as build_order
from workchain import MongoAuditLogger, MongoWorkflowStore, Workflow, WorkflowEngine
from workchain.contrib.fastapi import create_workchain_router


def _auto_tags(wf: Workflow) -> list[str]:
    """Derive feature tags from a Workflow object.

    Detects parallelism by computing depth tiers from the dependency graph.
    A workflow is "sequential" if every tier has exactly one step (no two
    steps share the same depth).  Steps may depend on non-adjacent
    predecessors for data access while still forming a linear chain.
    """
    tags: list[str] = []
    dep_map: dict[str, list[str]] = {s.name: s.depends_on or [] for s in wf.steps}
    depths: dict[str, int] = {}
    for s in wf.steps:
        parents = dep_map.get(s.name, [])
        depths[s.name] = (max(depths.get(p, 0) for p in parents) + 1) if parents else 0
    tier_counts: dict[int, int] = {}
    for d in depths.values():
        tier_counts[d] = tier_counts.get(d, 0) + 1
    parallel = any(c > 1 for c in tier_counts.values())

    if parallel:
        tags.append("step dependencies")
        tags.append("parallel execution")
    elif any(len(s.depends_on or []) > 0 for s in wf.steps):
        tags.append("sequential")
    else:
        tags.append("sequential")

    if any(s.is_async for s in wf.steps):
        tags.append("async polling")
    if any(s.retry_policy.max_attempts > 1 for s in wf.steps):
        tags.append("retry")
    return tags

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    force=True,
)

# ---------------------------------------------------------------------------
# Example workflow definitions
# ---------------------------------------------------------------------------

_EXAMPLE_DEFS: list[tuple[str, str, str, list[dict], object]] = [
    (
        "customer_onboarding",
        "Customer Onboarding",
        "Validate email, create account, provision resources (async), send welcome email",
        [{"name": "email", "label": "Email", "default": "alice@example.com", "type": "text"}],
        lambda params: build_onboarding(email=params["email"]),
    ),
    (
        "data_pipeline_etl",
        "Data Pipeline ETL",
        "28-step data-lakehouse pipeline: 5 parallel ingests (Postgres/Salesforce/S3/Kafka/Stripe), "
        "per-source schema validation, fan-in landing zone, PII/quality branches, async enrichment "
        "(GeoIP + user profiles), sessionization, aggregation, async feature store + Snowflake + "
        "Elasticsearch loads, dashboard publish, and downstream notification",
        [
            {"name": "postgres_dsn", "label": "Postgres DSN", "default": "postgres://pg-demo:5432/core", "type": "text"},
            {"name": "kafka_bootstrap", "label": "Kafka bootstrap", "default": "kafka-demo:9092", "type": "text"},
            {"name": "s3_bucket", "label": "S3 raw events bucket", "default": "acme-demo-raw", "type": "text"},
            {"name": "lake_bucket", "label": "Lake bronze bucket", "default": "acme-demo-lake", "type": "text"},
            {"name": "snowflake_warehouse", "label": "Snowflake warehouse", "default": "DEMO_LOAD_WH", "type": "text"},
        ],
        lambda params: build_etl(
            postgres_dsn=params["postgres_dsn"],
            kafka_bootstrap=params["kafka_bootstrap"],
            s3_bucket=params["s3_bucket"],
            lake_bucket=params["lake_bucket"],
            snowflake_warehouse=params["snowflake_warehouse"],
        ),
    ),
    (
        "ci_cd_pipeline",
        "CI/CD Pipeline",
        "Lint, 3 asymmetric lanes (unit tests / security+compliance / build+deploy), cross-lane join, notify+dashboard",
        [
            {"name": "repo", "label": "Repository", "default": "myorg/myapp", "type": "text"},
            {"name": "branch", "label": "Branch", "default": "main", "type": "text"},
        ],
        lambda params: build_ci_cd(repo=params["repo"], branch=params.get("branch", "main")),
    ),
    (
        "infra_provisioning",
        "Infrastructure Provisioning",
        "VPC || provision DB (concurrent), deploy app (async), DNS, TLS cert (async), health check",
        [
            {"name": "domain", "label": "Domain", "default": "app.example.com", "type": "text"},
            {"name": "image", "label": "Container Image", "default": "myorg/app:latest", "type": "text"},
            {"name": "region", "label": "Region", "default": "us-east-1", "type": "text"},
        ],
        lambda params: build_infra(
            domain=params["domain"], image=params["image"], region=params.get("region", "us-east-1")
        ),
    ),
    (
        "incident_response",
        "Incident Response",
        "Create ticket, page on-call (retry), diagnostics, remediate (async), verify, close",
        [
            {"name": "service_name", "label": "Service", "default": "payment-api", "type": "text"},
            {"name": "severity", "label": "Severity", "default": "high", "type": "text"},
            {"name": "description", "label": "Description", "default": "Elevated error rate on checkout", "type": "text"},
        ],
        lambda params: build_incident(
            service_name=params["service_name"],
            severity=params["severity"],
            description=params["description"],
        ),
    ),
    (
        "ml_training",
        "ML Model Training",
        "Prepare dataset, train/test split, train model (async — times out), evaluate, publish",
        [
            {"name": "dataset_name", "label": "Dataset", "default": "imagenet-mini", "type": "text"},
            {"name": "model_type", "label": "Model", "default": "resnet50", "type": "text"},
        ],
        lambda params: build_ml(
            dataset_name=params["dataset_name"],
            model_type=params["model_type"],
        ),
    ),
    (
        "media_processing",
        "Media Processing",
        "Ingest, audio branch (extract → normalize || waveform), video branches (transcode → thumbnail), cross-join, HLS package, CDN || catalog",
        [
            {"name": "filename", "label": "Filename", "default": "video.mp4", "type": "text"},
            {"name": "content_type", "label": "Content Type", "default": "video/mp4", "type": "text"},
        ],
        lambda params: build_media(
            filename=params["filename"],
            content_type=params["content_type"],
        ),
    ),
    (
        "order_fulfillment",
        "Order Fulfillment",
        "Validate, inventory || shipping (parallel), payment (async), reserve, pack, ship (async), confirm",
        [
            {"name": "order_id", "label": "Order ID", "default": "ORD-001", "type": "text"},
            {"name": "customer_email", "label": "Customer Email", "default": "alice@example.com", "type": "text"},
            {"name": "destination_zip", "label": "ZIP Code", "default": "90210", "type": "text"},
            {"name": "shipping_method", "label": "Shipping", "default": "standard", "type": "text"},
        ],
        lambda params: build_order(
            order_id=params["order_id"],
            customer_email=params["customer_email"],
            line_items=[{"sku": "WIDGET-A", "quantity": 2}, {"sku": "GADGET-B", "quantity": 1}],
            destination_zip=params.get("destination_zip", "10001"),
            shipping_method=params.get("shipping_method", "standard"),
        ),
    ),
]


def _build_examples() -> dict:
    """Build EXAMPLES dict with auto-generated tags and step counts."""
    # Build sample workflows using field defaults to derive tags and step counts
    examples = {}
    for key, title, description, fields, builder in _EXAMPLE_DEFS:
        defaults = {f["name"]: f["default"] for f in fields}
        sample_wf = builder(defaults)
        examples[key] = {
            "title": title,
            "description": description,
            "steps": len(sample_wf.steps),
            "tags": _auto_tags(sample_wf),
            "fields": fields,
            "builder": builder,
        }
    return examples


EXAMPLES = _build_examples()

# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

_client = AsyncMongoMockClient()
_db = _client["workchain_harness"]
audit_logger = MongoAuditLogger(_db)
store = MongoWorkflowStore(_db, lock_ttl_seconds=10, audit_logger=audit_logger, instance_id="harness-001")


@asynccontextmanager
async def lifespan(application: FastAPI):  # noqa: ARG001
    await store.ensure_indexes()
    await audit_logger.ensure_indexes()
    async with WorkflowEngine(
        store,
        instance_id="harness-001",
        claim_interval=0.5,
        heartbeat_interval=2.0,
        sweep_interval=5.0,
        step_stuck_seconds=30.0,
        max_concurrent=10,
        context={"db": _db, "store": store, "audit_logger": audit_logger},
    ) as engine:
        app.state.engine = engine
        yield


app = FastAPI(title="Workchain Test Harness", lifespan=lifespan)

# Static assets
_static_dir = Path(__file__).resolve().parent.parent / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

# Mount reusable workflow endpoints from contrib
app.include_router(create_workchain_router(store, audit_logger), prefix="/api/workflows", tags=["workflows"])

# ---------------------------------------------------------------------------
# Example-specific routes (workflow creation from templates)
# ---------------------------------------------------------------------------


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
<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
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
  .card .step-count { font-size: 0.85rem; color: #4b5563; margin-bottom: 0.5rem; }
  .card .tags { display: flex; gap: 0.35rem; flex-wrap: wrap; margin-bottom: 0.75rem; }
  .card .tag {
    font-size: 0.72rem; font-weight: 600; padding: 0.15em 0.55em;
    border-radius: 4px; letter-spacing: 0.02em;
  }
  .tag-sequential         { background: rgba(107,114,128,0.15); color: #9ca3af; border: 1px solid rgba(107,114,128,0.2); }
  .tag-async-polling      { background: rgba(251,191,36,0.1);  color: #fbbf24; border: 1px solid rgba(251,191,36,0.2); }
  .tag-retry              { background: rgba(248,113,113,0.1); color: #f87171; border: 1px solid rgba(248,113,113,0.2); }
  .tag-multi-instance     { background: rgba(99,102,241,0.1);  color: #a5b4fc; border: 1px solid rgba(99,102,241,0.2); }
  .tag-idempotent         { background: rgba(52,211,153,0.1);  color: #34d399; border: 1px solid rgba(52,211,153,0.2); }
  .tag-step-dependencies  { background: rgba(192,132,252,0.1); color: #c084fc; border: 1px solid rgba(192,132,252,0.2); }
  .tag-parallel-execution { background: rgba(56,189,248,0.1);  color: #38bdf8; border: 1px solid rgba(56,189,248,0.2); }
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

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}
function escAttr(s) { return esc(s).replace(/"/g, '&quot;'); }

function renderExamples() {
  const container = document.getElementById('examples');
  container.innerHTML = '';
  for (const [key, ex] of Object.entries(EXAMPLES)) {
    const card = document.createElement('div');
    card.className = 'card';
    let fieldsHtml = '';
    for (const f of ex.fields) {
      fieldsHtml += `<div class="field"><label>${esc(f.label)}</label><input name="${escAttr(f.name)}" value="${escAttr(f.default)}" data-example="${escAttr(key)}"></div>`;
    }
    const tagsHtml = (ex.tags || []).map(t => {
      const cls = 'tag-' + t.replace(/\\s+/g, '-');
      return `<span class="tag ${esc(cls)}">${esc(t)}</span>`;
    }).join('');
    card.innerHTML = `
      <h3>${esc(ex.title)}</h3>
      <div class="desc">${esc(ex.description)}</div>
      <div class="step-count">${ex.steps} steps</div>
      ${tagsHtml ? `<div class="tags">${tagsHtml}</div>` : ''}
      ${fieldsHtml}
      <button class="run-btn" onclick="runWorkflow('${escAttr(key)}', this)">Run</button>
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
        ? `<a href="/api/workflows/${wf.id}/report" target="_blank">View Report</a>`
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
    const res = await fetch(`/api/workflows/${wfId}/cancel`, { method: 'POST' });
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
            "tags": ex.get("tags", []),
            "fields": ex["fields"],
        }

    page = LANDING_HTML.replace("EXAMPLES_JSON", json.dumps(examples_json))
    return HTMLResponse(page)
