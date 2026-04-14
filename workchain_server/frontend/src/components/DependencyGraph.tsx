import { useMemo } from "react";
import dagre from "dagre";
import {
  Clock, Loader, CheckCircle2, XCircle, AlertTriangle,
} from "lucide-react";
import type { StepDetail } from "../api/types";

interface DependencyGraphProps {
  steps: StepDetail[];
  tiers: string[][];
  dependencies: Record<string, string[]>;
  onStepClick?: (stepName: string) => void;
}

const STATUS_CLASSES: Record<string, string> = {
  pending: "dep-step--pending",
  submitted: "dep-step--running",
  running: "dep-step--running",
  blocked: "dep-step--blocked",
  completed: "dep-step--completed",
  failed: "dep-step--failed",
};

const STATUS_ICONS: Record<string, React.ReactNode> = {
  pending: <Clock size={12} />,
  submitted: <Loader size={12} className="dep-step__spin" />,
  running: <Loader size={12} className="dep-step__spin" />,
  blocked: <AlertTriangle size={12} />,
  completed: <CheckCircle2 size={12} />,
  failed: <XCircle size={12} />,
};

const NODE_W = 160;
const NODE_H = 68;
const PADDING = 40;

interface LayoutNode {
  name: string;
  x: number;
  y: number;
}

interface LayoutEdge {
  source: string;
  target: string;
  points: Array<{ x: number; y: number }>;
}

function computeLayout(
  steps: StepDetail[],
  dependencies: Record<string, string[]>,
): { nodes: LayoutNode[]; edges: LayoutEdge[]; width: number; height: number } {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", nodesep: 20, ranksep: 60, marginx: PADDING, marginy: PADDING });
  g.setDefaultEdgeLabel(() => ({}));

  for (const s of steps) {
    g.setNode(s.name, { width: NODE_W, height: NODE_H });
  }

  for (const s of steps) {
    const deps = dependencies[s.name] || [];
    for (const dep of deps) {
      g.setEdge(dep, s.name);
    }
  }

  dagre.layout(g);

  const nodes: LayoutNode[] = [];
  for (const name of g.nodes()) {
    const n = g.node(name);
    if (n) nodes.push({ name, x: n.x - NODE_W / 2, y: n.y - NODE_H / 2 });
  }

  const edges: LayoutEdge[] = [];
  for (const e of g.edges()) {
    const ed = g.edge(e);
    if (ed) edges.push({ source: e.v, target: e.w, points: ed.points || [] });
  }

  const graphInfo = g.graph();
  return {
    nodes,
    edges,
    width: (graphInfo?.width ?? 600) + PADDING,
    height: (graphInfo?.height ?? 200) + PADDING,
  };
}

function edgePath(points: Array<{ x: number; y: number }>): string {
  if (points.length === 0) return "";
  const [first, ...rest] = points;
  let d = `M ${first.x} ${first.y}`;
  for (const p of rest) d += ` L ${p.x} ${p.y}`;
  return d;
}

export function DependencyGraph({ steps, dependencies, onStepClick }: DependencyGraphProps) {
  const stepMap = new Map(steps.map((s) => [s.name, s]));

  const layout = useMemo(
    () => computeLayout(steps, dependencies),
    [steps, dependencies],
  );

  return (
    <div className="dep-graph-inline">
      <div
        className="dep-graph-inline__canvas"
        style={{ width: layout.width, height: layout.height, position: "relative" }}
      >
        {/* Edges */}
        <svg
          className="dep-graph-inline__edges"
          width={layout.width}
          height={layout.height}
          style={{ position: "absolute", top: 0, left: 0, pointerEvents: "none" }}
        >
          <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5"
              markerWidth="6" markerHeight="6" orient="auto-start-reverse"
              fill="var(--border-structure)">
              <path d="M 0 0 L 10 5 L 0 10 z" />
            </marker>
            <marker id="arrow-completed" viewBox="0 0 10 10" refX="10" refY="5"
              markerWidth="6" markerHeight="6" orient="auto-start-reverse"
              fill="var(--c-completed)">
              <path d="M 0 0 L 10 5 L 0 10 z" />
            </marker>
            <marker id="arrow-waiting" viewBox="0 0 10 10" refX="10" refY="5"
              markerWidth="6" markerHeight="6" orient="auto-start-reverse"
              fill="var(--c-pending)">
              <path d="M 0 0 L 10 5 L 0 10 z" />
            </marker>
          </defs>
          {layout.edges.map((e, i) => {
            const source = stepMap.get(e.source);
            const target = stepMap.get(e.target);
            // An edge "fires" once its source step completes — the
            // dependency has been satisfied and its output has flowed
            // downstream. We treat it as completed unless the target is
            // still PENDING (engine hasn't picked it up yet), in which
            // case it renders as a waiting/marching-ants edge.
            const sourceDone = source?.status === "completed";
            const isWaiting = sourceDone && target?.status === "pending";
            const isCompleted = sourceDone && !isWaiting;
            let cls = "dep-graph-inline__edge";
            if (isCompleted) cls += " dep-graph-inline__edge--completed";
            else if (isWaiting) cls += " dep-graph-inline__edge--waiting";
            return (
              <path
                key={i}
                d={edgePath(e.points)}
                fill="none"
                className={cls}
                strokeWidth={isCompleted || isWaiting ? 1.5 : 1}
                markerEnd={
                  isCompleted
                    ? "url(#arrow-completed)"
                    : isWaiting
                      ? "url(#arrow-waiting)"
                      : "url(#arrow)"
                }
              />
            );
          })}
        </svg>

        {/* Nodes */}
        {layout.nodes.map((ln) => {
          const step = stepMap.get(ln.name);
          if (!step) return null;
          const cls = STATUS_CLASSES[step.status] || "";
          const modeCls = step.is_async ? "dep-step--async" : "dep-step--sync";

          return (
            <button
              key={ln.name}
              className={`dep-step ${cls} ${modeCls}`}
              style={{
                position: "absolute",
                left: ln.x,
                top: ln.y,
                width: NODE_W,
                height: NODE_H,
              }}
              onClick={() => onStepClick?.(ln.name)}
              title={`${ln.name} (${step.status})`}
            >
              <div className="dep-step__top">
                <span className="dep-step__icon">
                  {STATUS_ICONS[step.status]}
                </span>
                <span className="dep-step__name">{ln.name}</span>
              </div>
              <div className="dep-step__info">
                <span className="dep-step__status">{step.status}</span>
                {step.attempt > 1 && (
                  <span className="dep-step__attempt">
                    attempt {step.attempt}/{step.retry_policy.max_attempts}
                  </span>
                )}
              </div>
              {step.is_async && (
                <div className="dep-step__info">
                  <span className="dep-step__mode-tag">
                    async
                  </span>
                  {step.poll_count > 0 && (
                    <span className="dep-step__attempt">
                      poll {step.poll_count}
                    </span>
                  )}
                </div>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
