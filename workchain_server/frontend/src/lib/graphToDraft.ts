import type { Edge, Node } from "reactflow";
import type { StepDraft, WorkflowDraft } from "../api/types";

export interface StepNodeData {
  handlerName: string;
  stepName: string;
  configValues: Record<string, unknown>;
}

export type StepNode = Node<StepNodeData>;

/**
 * Convert a React Flow graph into a WorkflowDraft suitable for
 * POST /api/v1/workflows.
 *
 * Edge semantics: edge `source -> target` means "target depends on source".
 * Nodes are emitted in topological order for stability (not required by the
 * backend, but predictable when errors reference a step index).
 */
export function graphToDraft(
  workflowName: string,
  nodes: StepNode[],
  edges: Edge[],
): WorkflowDraft {
  const dependsOn = new Map<string, string[]>();
  for (const node of nodes) dependsOn.set(node.id, []);

  const nameById = new Map<string, string>();
  for (const node of nodes) nameById.set(node.id, node.data.stepName);

  for (const edge of edges) {
    const targetDeps = dependsOn.get(edge.target);
    const sourceName = nameById.get(edge.source);
    if (targetDeps && sourceName && !targetDeps.includes(sourceName)) {
      targetDeps.push(sourceName);
    }
  }

  const ordered = topoSort(nodes, edges);

  const steps: StepDraft[] = ordered.map((node) => ({
    name: node.data.stepName,
    handler: node.data.handlerName,
    config: node.data.configValues,
    depends_on: dependsOn.get(node.id) ?? [],
  }));

  return { name: workflowName, steps };
}

function topoSort(nodes: StepNode[], edges: Edge[]): StepNode[] {
  const inDegree = new Map<string, number>();
  const dependents = new Map<string, string[]>();
  const byId = new Map<string, StepNode>();
  for (const n of nodes) {
    inDegree.set(n.id, 0);
    dependents.set(n.id, []);
    byId.set(n.id, n);
  }
  for (const e of edges) {
    dependents.get(e.source)?.push(e.target);
    inDegree.set(e.target, (inDegree.get(e.target) ?? 0) + 1);
  }
  const queue: string[] = [];
  for (const [id, deg] of inDegree) if (deg === 0) queue.push(id);
  const out: StepNode[] = [];
  while (queue.length) {
    const id = queue.shift()!;
    const node = byId.get(id);
    if (node) out.push(node);
    for (const child of dependents.get(id) ?? []) {
      const d = (inDegree.get(child) ?? 0) - 1;
      inDegree.set(child, d);
      if (d === 0) queue.push(child);
    }
  }
  // Append any nodes that were part of a cycle so the caller can still
  // surface them (draftValidate will flag the cycle separately).
  if (out.length < nodes.length) {
    const emitted = new Set(out.map((n) => n.id));
    for (const n of nodes) if (!emitted.has(n.id)) out.push(n);
  }
  return out;
}
