import { Link } from "react-router-dom";
import {
  ArrowRight,
  Boxes,
  Cpu,
  GitBranch,
  LayoutDashboard,
  Lock,
  PenTool,
  Radio,
  ScrollText,
  ShieldCheck,
  Workflow,
  Zap,
} from "lucide-react";

const FEATURES = [
  {
    icon: <Lock size={20} />,
    title: "Per-step distributed locks",
    body: "Fence-token optimistic locking on every step. Independent steps run concurrently across engine instances without coordination.",
  },
  {
    icon: <ShieldCheck size={20} />,
    title: "Crash-safe state machine",
    body: "Write-ahead markers before execution. Recovery replays via verify_completion, completeness checks, or idempotent re-runs.",
  },
  {
    icon: <Boxes size={20} />,
    title: "Typed configs & results",
    body: "StepConfig and StepResult subclasses round-trip through MongoDB. Downstream steps cast() dependency results with type safety.",
  },
  {
    icon: <Radio size={20} />,
    title: "Async polling built in",
    body: "@async_step submits, @completeness_check polls. Adaptive retry_after + backoff. Any engine instance can pick up the next cycle.",
  },
  {
    icon: <GitBranch size={20} />,
    title: "DAG-first dependencies",
    body: "depends_on declared on decorators. Validator auto-wires step fields, catches cycles, unknown refs, and missing handler deps.",
  },
  {
    icon: <ScrollText size={20} />,
    title: "Structured audit trail",
    body: "Every state transition emits a typed AuditEvent. Reconstruct flow diagrams, debug failures, and generate HTML execution reports.",
  },
];

const CODE_SAMPLE = `from workchain import step, async_step, completeness_check
from workchain.models import CheckResult, PollPolicy, RetryPolicy

@step(retry=RetryPolicy(max_attempts=5, wait_seconds=1.0))
async def validate_email(config: ValidateConfig, results) -> ValidateResult:
    return ValidateResult(valid=check_mx(config.email))

@async_step(
    completeness_check=check_provision,
    poll=PollPolicy(interval=10.0, timeout=600.0),
    depends_on=["validate_email"],
)
async def provision_resources(config, results) -> ProvisionResult:
    job_id = start_provisioning_job(config.tier)
    return ProvisionResult(job_id=job_id)

@completeness_check()
async def check_provision(config, results, result) -> CheckResult:
    status = get_job_status(result.job_id)
    return CheckResult(
        complete=status.done,
        progress=status.percent / 100.0,
    )`;

export function LandingPage() {
  return (
    <div className="landing">
      <div className="landing__grid" />
      <div className="landing__vignette" />

      <header className="landing__nav">
        <div className="landing__brand">
          <Workflow size={22} />
          <span>workchain</span>
        </div>
        <nav className="landing__nav-links">
          <Link to="/dashboard" className="landing__nav-link">Dashboard</Link>
          <Link to="/workflows" className="landing__nav-link">Workflows</Link>
          <Link to="/designer" className="landing__nav-link">Designer</Link>
        </nav>
      </header>

      <main className="landing__content">
        {/* -------- HERO -------- */}
        <section className="landing__hero">
          <div className="landing__eyebrow">
            <Zap size={14} /> Persistent workflow engine for Python
          </div>
          <h1 className="landing__title">
            Durable, distributed,<br />
            <span className="landing__title-accent">type-safe</span> workflows.
          </h1>
          <p className="landing__lede">
            Build multi-step workflows with dependencies, async polling, crash recovery,
            and a full audit trail — backed by MongoDB, declared with decorators, and
            safe to run across any number of engine instances.
          </p>
          <div className="landing__ctas">
            <Link to="/dashboard" className="landing__cta landing__cta--primary">
              <LayoutDashboard size={16} /> Open dashboard
              <ArrowRight size={16} />
            </Link>
            <Link to="/designer" className="landing__cta landing__cta--secondary">
              <PenTool size={16} /> Try the designer
            </Link>
          </div>

          {/* Mini DAG visual */}
          <div className="landing__dag" aria-hidden="true">
            <svg viewBox="0 0 720 220" className="landing__dag-svg">
              <defs>
                <marker id="landing-arrow" viewBox="0 0 10 10" refX="10" refY="5"
                        markerWidth="6" markerHeight="6" orient="auto-start-reverse"
                        fill="var(--c-completed)">
                  <path d="M 0 0 L 10 5 L 0 10 z" />
                </marker>
                <marker id="landing-arrow-running" viewBox="0 0 10 10" refX="10" refY="5"
                        markerWidth="6" markerHeight="6" orient="auto-start-reverse"
                        fill="var(--c-running)">
                  <path d="M 0 0 L 10 5 L 0 10 z" />
                </marker>
              </defs>
              {/* Completed edges */}
              <path d="M 110 50 L 220 50" className="landing__edge landing__edge--completed" markerEnd="url(#landing-arrow)" />
              <path d="M 110 110 L 220 110" className="landing__edge landing__edge--completed" markerEnd="url(#landing-arrow)" />
              <path d="M 110 170 L 220 170" className="landing__edge landing__edge--completed" markerEnd="url(#landing-arrow)" />
              <path d="M 330 50 L 440 110" className="landing__edge landing__edge--completed" markerEnd="url(#landing-arrow)" />
              <path d="M 330 110 L 440 110" className="landing__edge landing__edge--completed" markerEnd="url(#landing-arrow)" />
              <path d="M 330 170 L 440 110" className="landing__edge landing__edge--completed" markerEnd="url(#landing-arrow)" />
              {/* Running edge */}
              <path d="M 550 110 L 660 110" className="landing__edge landing__edge--running" markerEnd="url(#landing-arrow-running)" />
              {/* Nodes */}
              <g className="landing__node landing__node--completed">
                <rect x="20" y="25" width="90" height="50" rx="6" />
                <text x="65" y="55" textAnchor="middle">ingest_a</text>
              </g>
              <g className="landing__node landing__node--completed">
                <rect x="20" y="85" width="90" height="50" rx="6" />
                <text x="65" y="115" textAnchor="middle">ingest_b</text>
              </g>
              <g className="landing__node landing__node--completed">
                <rect x="20" y="145" width="90" height="50" rx="6" />
                <text x="65" y="175" textAnchor="middle">ingest_c</text>
              </g>
              <g className="landing__node landing__node--completed">
                <rect x="220" y="25" width="110" height="50" rx="6" />
                <text x="275" y="55" textAnchor="middle">validate</text>
              </g>
              <g className="landing__node landing__node--completed">
                <rect x="220" y="85" width="110" height="50" rx="6" />
                <text x="275" y="115" textAnchor="middle">validate</text>
              </g>
              <g className="landing__node landing__node--completed">
                <rect x="220" y="145" width="110" height="50" rx="6" />
                <text x="275" y="175" textAnchor="middle">validate</text>
              </g>
              <g className="landing__node landing__node--completed">
                <rect x="440" y="85" width="110" height="50" rx="6" />
                <text x="495" y="115" textAnchor="middle">land_raw</text>
              </g>
              <g className="landing__node landing__node--running">
                <rect x="660" y="85" width="40" height="50" rx="6" />
                <circle cx="680" cy="110" r="6" className="landing__pulse" />
              </g>
            </svg>
          </div>
        </section>

        {/* -------- FEATURES -------- */}
        <section className="landing__section">
          <div className="landing__section-header">
            <span className="landing__section-tag">Built for production</span>
            <h2 className="landing__section-title">Every primitive you need.<br />None you don't.</h2>
          </div>
          <div className="landing__features">
            {FEATURES.map((f) => (
              <div key={f.title} className="landing__feature">
                <div className="landing__feature-icon">{f.icon}</div>
                <h3 className="landing__feature-title">{f.title}</h3>
                <p className="landing__feature-body">{f.body}</p>
              </div>
            ))}
          </div>
        </section>

        {/* -------- CODE SAMPLE -------- */}
        <section className="landing__section">
          <div className="landing__code-layout">
            <div className="landing__code-copy">
              <span className="landing__section-tag">Decorator-driven</span>
              <h2 className="landing__section-title">Declare once.<br />Run everywhere.</h2>
              <p className="landing__code-lede">
                Decorator metadata auto-propagates to the Step model at workflow
                construction time. Retry, poll, and depends_on flow through to
                the engine without a single line of glue code.
              </p>
              <ul className="landing__code-bullets">
                <li><Cpu size={14} /> Handler names auto-generated from module + qualname</li>
                <li><Cpu size={14} /> Typed configs pass through template CRUD and the designer</li>
                <li><Cpu size={14} /> Completeness checks carry their own retry policy</li>
              </ul>
            </div>
            <pre className="landing__code">
              <code>{CODE_SAMPLE}</code>
            </pre>
          </div>
        </section>

        {/* -------- CTA BAND -------- */}
        <section className="landing__cta-band">
          <h2 className="landing__cta-band-title">
            Eight production-style example workflows<br />
            <span className="landing__cta-band-accent">ready to launch.</span>
          </h2>
          <p className="landing__cta-band-lede">
            Customer onboarding, CI/CD, media processing, ML training, incident
            response, infrastructure provisioning, order fulfillment, and a
            28-step data lakehouse pipeline — seeded into your instance on
            first start and visible in the template catalog.
          </p>
          <Link to="/dashboard" className="landing__cta landing__cta--primary">
            Browse templates <ArrowRight size={16} />
          </Link>
        </section>

        {/* -------- FOOTER -------- */}
        <footer className="landing__footer">
          <div className="landing__footer-brand">
            <Workflow size={16} /> workchain
          </div>
          <div className="landing__footer-meta">
            Python · Pydantic · Motor · MongoDB
          </div>
        </footer>
      </main>
    </div>
  );
}
