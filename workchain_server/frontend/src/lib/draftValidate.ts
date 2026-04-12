import type { Edge } from "reactflow";
import type { HandlerDescriptor } from "../api/types";
import { isStepNode, type DesignerNode } from "./graphToDraft";

export interface DraftIssue {
  nodeId?: string;
  message: string;
}

/**
 * Client-side checks that catch obvious problems before POST /workflows:
 * empty workflow, missing step names, duplicate step names, orphan handlers,
 * dependency cycles (via Kahn's algorithm), and missing handler-declared
 * dependencies.
 *
 * Anchor and async-block nodes are excluded — only step nodes are validated.
 * Backend still performs the authoritative validation, but surfacing errors
 * locally means the designer never has to round-trip for obvious issues.
 */
export function draftValidate(
  workflowName: string,
  nodes: DesignerNode[],
  edges: Edge[],
  handlers?: HandlerDescriptor[],
): DraftIssue[] {
  const issues: DraftIssue[] = [];
  const stepNodes = nodes.filter(isStepNode);

  if (!workflowName.trim()) {
    issues.push({ message: "Workflow needs a name." });
  }
  if (stepNodes.length === 0) {
    issues.push({ message: "Add at least one step to the canvas." });
    return issues;
  }

  const stepIds = new Set(stepNodes.map((n) => n.id));
  const stepEdges = edges.filter(
    (e) => stepIds.has(e.source) && stepIds.has(e.target),
  );

  const seen = new Map<string, string>(); // stepName -> nodeId
  for (const node of stepNodes) {
    const name = node.data.stepName.trim();
    if (!name) {
      issues.push({ nodeId: node.id, message: "Step name cannot be empty." });
      continue;
    }
    const existing = seen.get(name);
    if (existing) {
      issues.push({
        nodeId: node.id,
        message: `Duplicate step name '${name}' — each step needs a unique name.`,
      });
    } else {
      seen.set(name, node.id);
    }
    if (!node.data.handlerName) {
      issues.push({
        nodeId: node.id,
        message: "Step is missing a handler.",
      });
    }
  }

  // Kahn's algorithm cycle detection.
  const inDegree = new Map<string, number>();
  const dependents = new Map<string, string[]>();
  for (const n of stepNodes) {
    inDegree.set(n.id, 0);
    dependents.set(n.id, []);
  }
  for (const e of stepEdges) {
    if (e.source === e.target) {
      issues.push({
        nodeId: e.target,
        message: "Self-dependency is not allowed.",
      });
      continue;
    }
    dependents.get(e.source)?.push(e.target);
    inDegree.set(e.target, (inDegree.get(e.target) ?? 0) + 1);
  }
  const queue: string[] = [];
  for (const [id, deg] of inDegree) if (deg === 0) queue.push(id);
  let visited = 0;
  while (queue.length) {
    const id = queue.shift()!;
    visited += 1;
    for (const child of dependents.get(id) ?? []) {
      const d = (inDegree.get(child) ?? 0) - 1;
      inDegree.set(child, d);
      if (d === 0) queue.push(child);
    }
  }
  if (visited < stepNodes.length) {
    issues.push({ message: "Dependency cycle detected among steps." });
  }

  // Check handler-declared dependency requirements.
  // Build a map of step name → set of dependency step names (from edges).
  if (handlers && handlers.length > 0) {
    const handlerMap = new Map(handlers.map((h) => [h.name, h]));
    const nameToNodeId = new Map(stepNodes.map((n) => [n.data.stepName, n.id]));
    const nodeIdToName = new Map(stepNodes.map((n) => [n.id, n.data.stepName]));

    // For each step node, collect its actual dependencies from edges
    const actualDeps = new Map<string, Set<string>>();
    for (const n of stepNodes) actualDeps.set(n.id, new Set());
    for (const e of stepEdges) {
      const depName = nodeIdToName.get(e.source);
      if (depName) actualDeps.get(e.target)?.add(depName);
    }

    for (const node of stepNodes) {
      const descriptor = handlerMap.get(node.data.handlerName);
      if (!descriptor?.depends_on) continue;
      const actual = actualDeps.get(node.id) ?? new Set();
      const missing = descriptor.depends_on.filter((d) => !actual.has(d));
      if (missing.length > 0) {
        // Check if the required steps even exist on the canvas
        const onCanvas = missing.filter((d) => nameToNodeId.has(d));
        const notOnCanvas = missing.filter((d) => !nameToNodeId.has(d));
        if (notOnCanvas.length > 0) {
          issues.push({
            nodeId: node.id,
            message: `Needs step${notOnCanvas.length > 1 ? "s" : ""} not on canvas: ${notOnCanvas.join(", ")}`,
          });
        }
        if (onCanvas.length > 0) {
          issues.push({
            nodeId: node.id,
            message: `Connect from: ${onCanvas.join(", ")}`,
          });
        }
      }
    }
  }

  return issues;
}
