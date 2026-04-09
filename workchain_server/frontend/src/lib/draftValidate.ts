import type { Edge } from "reactflow";
import type { StepNode } from "./graphToDraft";

export interface DraftIssue {
  nodeId?: string;
  message: string;
}

/**
 * Client-side checks that catch obvious problems before POST /workflows:
 * empty workflow, missing step names, duplicate step names, orphan handlers,
 * and dependency cycles (via Kahn's algorithm).
 *
 * Backend still performs the authoritative validation, but surfacing errors
 * locally means the designer never has to round-trip for obvious issues.
 */
export function draftValidate(
  workflowName: string,
  nodes: StepNode[],
  edges: Edge[],
): DraftIssue[] {
  const issues: DraftIssue[] = [];

  if (!workflowName.trim()) {
    issues.push({ message: "Workflow needs a name." });
  }
  if (nodes.length === 0) {
    issues.push({ message: "Add at least one step to the canvas." });
    return issues;
  }

  const seen = new Map<string, string>(); // stepName -> nodeId
  for (const node of nodes) {
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
  for (const n of nodes) {
    inDegree.set(n.id, 0);
    dependents.set(n.id, []);
  }
  for (const e of edges) {
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
  if (visited < nodes.length) {
    issues.push({ message: "Dependency cycle detected among steps." });
  }

  return issues;
}
