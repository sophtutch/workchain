/**
 * Convert a WorkflowTemplate into React Flow nodes and edges for the designer canvas.
 */

import type { Edge } from "reactflow";
import type { HandlerDescriptor, WorkflowTemplate } from "../api/types";
import {
  START_NODE_ID,
  END_NODE_ID,
  type DesignerNode,
} from "./graphToDraft";

/**
 * Build a set of React Flow nodes and edges from a workflow template.
 *
 * Creates step nodes for each template step, dependency edges from
 * ``depends_on``, and connects root/leaf steps to the START/END anchors.
 */
export function templateToGraph(
  template: WorkflowTemplate,
  handlerMap: Map<string, HandlerDescriptor>,
): { nodes: DesignerNode[]; edges: Edge[] } {
  // Anchor nodes.
  const startNode: DesignerNode = {
    id: START_NODE_ID,
    type: "anchor",
    position: { x: 0, y: 200 },
    data: { label: "START" },
    deletable: false,
    draggable: true,
  };
  const endNode: DesignerNode = {
    id: END_NODE_ID,
    type: "anchor",
    position: { x: 800, y: 200 },
    data: { label: "END" },
    deletable: false,
    draggable: true,
  };

  // Step nodes.  Prefix IDs to avoid collision with reserved anchor IDs.
  const stepId = (name: string) =>
    name === START_NODE_ID || name === END_NODE_ID ? `step_${name}` : name;

  const stepNodes: DesignerNode[] = template.steps.map((step, i) => {
    const handler = handlerMap.get(step.handler);
    return {
      id: stepId(step.name),
      type: "step" as const,
      position: { x: 100, y: 100 + i * 80 },
      data: {
        handlerName: step.handler,
        stepName: step.name,
        configValues: (step.config ?? {}) as Record<string, unknown>,
        handlerDescription: handler?.description ?? undefined,
        handlerIsAsync: handler?.is_async ?? false,
      },
    };
  });

  // Dependency edges.
  const edges: Edge[] = [];
  for (const step of template.steps) {
    if (step.depends_on) {
      for (const dep of step.depends_on) {
        edges.push({
          id: `${stepId(dep)}:result->${stepId(step.name)}:deps`,
          source: stepId(dep),
          target: stepId(step.name),
          sourceHandle: "result",
          targetHandle: "deps",
        });
      }
    }
  }

  // Connect START → root steps, leaf steps → END.
  const hasDownstream = new Set<string>();
  for (const step of template.steps) {
    if (step.depends_on) {
      for (const dep of step.depends_on) hasDownstream.add(dep);
    }
  }

  for (const step of template.steps) {
    const isRoot = !step.depends_on || step.depends_on.length === 0;
    if (isRoot) {
      edges.push({
        id: `${START_NODE_ID}:result->${stepId(step.name)}:deps`,
        source: START_NODE_ID,
        target: stepId(step.name),
        sourceHandle: "result",
        targetHandle: "deps",
      });
    }
    if (!hasDownstream.has(step.name)) {
      edges.push({
        id: `${stepId(step.name)}:result->${END_NODE_ID}:deps`,
        source: stepId(step.name),
        target: END_NODE_ID,
        sourceHandle: "result",
        targetHandle: "deps",
      });
    }
  }

  return {
    nodes: [startNode, endNode, ...stepNodes],
    edges,
  };
}
