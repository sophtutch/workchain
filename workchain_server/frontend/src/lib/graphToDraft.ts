import type { Edge, Node } from "reactflow";
import type { StepDraft, TemplateStep, WorkflowDraft } from "../api/types";
import type { BlockNodeData } from "../components/BlockNode";
// ---------------------------------------------------------------------------
// Node data types
// ---------------------------------------------------------------------------

export interface StepNodeData {
  handlerName: string;
  stepName: string;
  configValues: Record<string, unknown>;
  /** Short description for display on the canvas node. */
  handlerDescription?: string;
  /** Whether this is an async step (shown with an indicator). */
  handlerIsAsync?: boolean;
}

export interface AnchorNodeData {
  label: string;
}

export type DesignerNodeData = StepNodeData | AnchorNodeData | BlockNodeData;

/** Any node on the designer canvas (step, anchor, block). */
export type DesignerNode = Node<DesignerNodeData>;

/** A step node specifically (for graphToDraft / validation). */
export type StepNode = Node<StepNodeData>;

export function isStepNode(node: DesignerNode): node is StepNode {
  return node.type === "step";
}

export function isBlockNode(node: DesignerNode): boolean {
  return node.type === "block";
}

// ---------------------------------------------------------------------------
// IDs for the permanent Start / End anchor nodes
// ---------------------------------------------------------------------------

export const START_NODE_ID = "__start__";
export const END_NODE_ID = "__end__";

/** ID helpers for block-internal S/E anchors. */
export function blockStartId(blockId: string): string {
  return `${blockId}__s`;
}
export function blockEndId(blockId: string): string {
  return `${blockId}__e`;
}
export function isBlockAnchor(nodeId: string): boolean {
  return nodeId.endsWith("__s") || nodeId.endsWith("__e");
}

// ---------------------------------------------------------------------------
// Draft generation
// ---------------------------------------------------------------------------

/**
 * Convert a React Flow graph into a WorkflowDraft suitable for
 * POST /api/v1/workflows.
 *
 * Block-level edges are resolved to step-level depends_on:
 *   - Edge targeting a block → all "entry steps" inside the block
 *     (steps with no incoming edges from siblings in the same block)
 *     depend on the edge source.
 *   - Edge sourcing from a block → the edge target depends on all
 *     "exit steps" inside the block (steps with no outgoing edges
 *     to siblings in the same block).
 *
 * Anchor nodes and blocks themselves are filtered out — only step
 * nodes become workflow steps.
 */
export function graphToDraft(
  workflowName: string,
  nodes: DesignerNode[],
  edges: Edge[],
): WorkflowDraft {
  const { ordered, dependsOn } = resolveStepsWithDeps(nodes, edges);

  const steps: StepDraft[] = ordered.map((node) => ({
    name: node.data.stepName,
    handler: node.data.handlerName,
    config: node.data.configValues,
    depends_on: dependsOn.get(node.id) ?? [],
  }));

  return { name: workflowName, steps };
}

// ---------------------------------------------------------------------------
// Block boundary helpers
// ---------------------------------------------------------------------------

/**
 * Entry steps: steps inside a block connected from the block's S anchor.
 * Falls back to steps with no incoming edges from siblings if no S edges.
 */
function findEntrySteps(
  blockId: string,
  childrenOf: Map<string, Set<string>>,
  edges: Edge[],
  stepIds: Set<string>,
): string[] {
  const children = childrenOf.get(blockId);
  if (!children || children.size === 0) return [];

  // Prefer explicit S → step edges.
  const sId = blockStartId(blockId);
  const fromS = edges
    .filter((e) => e.source === sId && children.has(e.target))
    .map((e) => e.target);
  if (fromS.length > 0) return fromS;

  // Fallback: steps with no incoming edges from other internal steps.
  const hasInternalIncoming = new Set<string>();
  for (const e of edges) {
    if (children.has(e.target) && children.has(e.source) && stepIds.has(e.source)) {
      hasInternalIncoming.add(e.target);
    }
  }
  return [...children].filter((id) => !hasInternalIncoming.has(id));
}

/**
 * Exit steps: steps inside a block connected to the block's E anchor.
 * Falls back to steps with no outgoing edges to siblings if no E edges.
 */
function findExitSteps(
  blockId: string,
  childrenOf: Map<string, Set<string>>,
  edges: Edge[],
  stepIds: Set<string>,
): string[] {
  const children = childrenOf.get(blockId);
  if (!children || children.size === 0) return [];

  // Prefer explicit step → E edges.
  const eId = blockEndId(blockId);
  const toE = edges
    .filter((e) => e.target === eId && children.has(e.source))
    .map((e) => e.source);
  if (toE.length > 0) return toE;

  // Fallback: steps with no outgoing edges to other internal steps.
  const hasInternalOutgoing = new Set<string>();
  for (const e of edges) {
    if (children.has(e.source) && children.has(e.target) && stepIds.has(e.target)) {
      hasInternalOutgoing.add(e.source);
    }
  }
  return [...children].filter((id) => !hasInternalOutgoing.has(id));
}

// ---------------------------------------------------------------------------
// Topological sort
// ---------------------------------------------------------------------------

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
    if (!inDegree.has(e.source) || !inDegree.has(e.target)) continue;
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
  if (out.length < nodes.length) {
    const emitted = new Set(out.map((n) => n.id));
    for (const n of nodes) if (!emitted.has(n.id)) out.push(n);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Shared: resolve block edges + build ordered steps with depends_on
// ---------------------------------------------------------------------------

/**
 * Resolve block-level edges into step-to-step edges, build depends_on
 * maps, and return topo-sorted step nodes with their dependency lists.
 *
 * Shared by both ``graphToDraft`` and ``graphToTemplateSteps``.
 */
function resolveStepsWithDeps(
  nodes: DesignerNode[],
  edges: Edge[],
): { ordered: StepNode[]; dependsOn: Map<string, string[]> } {
  const stepNodes = nodes.filter(isStepNode);
  const stepIds = new Set(stepNodes.map((n) => n.id));
  const blockIds = new Set(
    nodes.filter((n) => n.type === "block").map((n) => n.id),
  );

  const childrenOf = new Map<string, Set<string>>();
  for (const id of blockIds) childrenOf.set(id, new Set());
  for (const n of stepNodes) {
    if (n.parentNode && childrenOf.has(n.parentNode)) {
      childrenOf.get(n.parentNode)!.add(n.id);
    }
  }

  const resolvedEdges: Edge[] = [];
  for (const edge of edges) {
    const srcIsBlock = blockIds.has(edge.source);
    const tgtIsBlock = blockIds.has(edge.target);
    const srcIsStep = stepIds.has(edge.source);
    const tgtIsStep = stepIds.has(edge.target);

    if (srcIsStep && tgtIsStep) {
      resolvedEdges.push(edge);
    } else if (srcIsBlock && tgtIsStep) {
      for (const exitId of findExitSteps(edge.source, childrenOf, edges, stepIds)) {
        resolvedEdges.push({ ...edge, id: `${exitId}->${edge.target}`, source: exitId });
      }
    } else if (srcIsStep && tgtIsBlock) {
      for (const entryId of findEntrySteps(edge.target, childrenOf, edges, stepIds)) {
        resolvedEdges.push({ ...edge, id: `${edge.source}->${entryId}`, target: entryId });
      }
    } else if (srcIsBlock && tgtIsBlock) {
      const exits = findExitSteps(edge.source, childrenOf, edges, stepIds);
      const entries = findEntrySteps(edge.target, childrenOf, edges, stepIds);
      for (const exitId of exits) {
        for (const entryId of entries) {
          resolvedEdges.push({ ...edge, id: `${exitId}->${entryId}`, source: exitId, target: entryId });
        }
      }
    }
  }

  const dependsOn = new Map<string, string[]>();
  for (const node of stepNodes) dependsOn.set(node.id, []);
  const nameById = new Map<string, string>();
  for (const node of stepNodes) nameById.set(node.id, node.data.stepName);

  for (const edge of resolvedEdges) {
    const targetDeps = dependsOn.get(edge.target);
    const sourceName = nameById.get(edge.source);
    if (targetDeps && sourceName && !targetDeps.includes(sourceName)) {
      targetDeps.push(sourceName);
    }
  }

  return { ordered: topoSort(stepNodes, resolvedEdges), dependsOn };
}

// ---------------------------------------------------------------------------
// Canvas → TemplateStep[] (for saving templates)
// ---------------------------------------------------------------------------

/**
 * Convert a React Flow graph into an array of :class:`TemplateStep` objects
 * suitable for ``PUT /api/v1/templates/{id}`` or ``POST /api/v1/templates``.
 */
export function graphToTemplateSteps(
  nodes: DesignerNode[],
  edges: Edge[],
): TemplateStep[] {
  const { ordered, dependsOn } = resolveStepsWithDeps(nodes, edges);
  return ordered.map((node) => ({
    name: node.data.stepName,
    handler: node.data.handlerName,
    config: node.data.configValues,
    depends_on: dependsOn.get(node.id) ?? [],
  }));
}
