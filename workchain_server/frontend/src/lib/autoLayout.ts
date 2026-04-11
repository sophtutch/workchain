import dagre from "dagre";
import type { Edge } from "reactflow";
import type { DesignerNode } from "./graphToDraft";

// Approximate node dimensions used by dagre for spacing.
// Must match or slightly exceed the actual rendered sizes.
// Tell dagre nodes are slightly larger than actual CSS size to guarantee
// visible clearance between nodes and edges.
const NODE_WIDTH = 240;
const NODE_HEIGHT = 120;
const ANCHOR_WIDTH = 100;
const ANCHOR_HEIGHT = 80;
const BLOCK_PADDING = 40;


/**
 * Apply dagre auto-layout to all top-level nodes (and within each block),
 * then assign smart handle IDs to edges based on relative node positions.
 *
 * Returns both the repositioned nodes and the edges with updated
 * ``sourceHandle`` / ``targetHandle`` properties.
 */
export function autoLayout(
  nodes: DesignerNode[],
  edges: Edge[],
): { nodes: DesignerNode[]; edges: Edge[] } {
  const rankdir = "LR";

  // Partition nodes by parent.
  const topLevel: DesignerNode[] = [];
  const byParent = new Map<string, DesignerNode[]>();

  for (const n of nodes) {
    if (n.parentNode) {
      const list = byParent.get(n.parentNode) ?? [];
      list.push(n);
      byParent.set(n.parentNode, list);
    } else {
      topLevel.push(n);
    }
  }

  // Layout each block's children first so we know block sizes.
  const updatedChildren = new Map<string, DesignerNode[]>();
  const blockSizes = new Map<string, { width: number; height: number }>();

  for (const [blockId, children] of byParent) {
    const childIds = new Set(children.map((n) => n.id));
    const internalEdges = edges.filter(
      (e) => childIds.has(e.source) && childIds.has(e.target),
    );

    const stepsAndAnchors = children.filter(
      (n) => n.type === "step" || n.type === "anchor",
    );
    if (stepsAndAnchors.length === 0) {
      updatedChildren.set(blockId, children);
      continue;
    }

    const { positioned, width, height } = layoutGraph(
      stepsAndAnchors,
      internalEdges,
      rankdir,
    );

    updatedChildren.set(blockId, positioned);
    blockSizes.set(blockId, {
      width: Math.round(width + BLOCK_PADDING * 2),
      height: Math.round(height + BLOCK_PADDING * 2),
    });
  }

  // Assign block sizes before top-level layout.
  const topWithSizes = topLevel.map((n) => {
    if (n.type === "block" && blockSizes.has(n.id)) {
      const size = blockSizes.get(n.id)!;
      return { ...n, style: { ...n.style, width: size.width, height: size.height } };
    }
    return n;
  });

  // Layout top-level nodes.
  const topIds = new Set(topWithSizes.map((n) => n.id));
  const topEdges = edges.filter((e) => topIds.has(e.source) && topIds.has(e.target));

  const { positioned: topPositioned } = layoutGraph(
    topWithSizes,
    topEdges,
    rankdir,
  );

  // Merge: top-level positioned nodes + offset children inside blocks.
  const result: DesignerNode[] = [];
  for (const n of topPositioned) {
    result.push(n);
    const children = updatedChildren.get(n.id);
    if (children) {
      const pad = Math.round(BLOCK_PADDING);
      for (const c of children) {
        result.push({
          ...c,
          position: {
            x: Math.round(c.position.x + pad),
            y: Math.round(c.position.y + pad),
          },
        });
      }
    }
  }

  // Assign smart handles to edges.
  const smartEdges = assignHandles(result, edges);

  return { nodes: result, edges: smartEdges };
}

// ---------------------------------------------------------------------------
// Dagre graph layout helper
// ---------------------------------------------------------------------------

function layoutGraph(
  nodes: DesignerNode[],
  edges: Edge[],
  rankdir: string,
): { positioned: DesignerNode[]; width: number; height: number } {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir, nodesep: 60, ranksep: 60, marginx: 20, marginy: 20 });

  for (const n of nodes) {
    const isAnchor = n.type === "anchor";
    const isBlock = n.type === "block";
    const w = isBlock
      ? ((n.style?.width as number) ?? 300)
      : isAnchor
        ? ANCHOR_WIDTH
        : NODE_WIDTH;
    const h = isBlock
      ? ((n.style?.height as number) ?? 160)
      : isAnchor
        ? ANCHOR_HEIGHT
        : NODE_HEIGHT;
    g.setNode(n.id, { width: w, height: h });
  }

  for (const e of edges) {
    if (g.hasNode(e.source) && g.hasNode(e.target)) {
      g.setEdge(e.source, e.target);
    }
  }

  dagre.layout(g);

  const positioned: DesignerNode[] = [];
  for (const n of nodes) {
    const pos = g.node(n.id);
    const isAnchor = n.type === "anchor";
    const isBlock = n.type === "block";
    const w = isBlock
      ? ((n.style?.width as number) ?? 300)
      : isAnchor
        ? ANCHOR_WIDTH
        : NODE_WIDTH;
    const h = isBlock
      ? ((n.style?.height as number) ?? 160)
      : isAnchor
        ? ANCHOR_HEIGHT
        : NODE_HEIGHT;
    positioned.push({
      ...n,
      position: { x: Math.round(pos.x - w / 2), y: Math.round(pos.y - h / 2) },
    });
  }

  const graphInfo = g.graph();
  return {
    positioned,
    width: graphInfo.width ?? 400,
    height: graphInfo.height ?? 200,
  };
}

// ---------------------------------------------------------------------------
// Handle assignment
// ---------------------------------------------------------------------------

/** Get the pixel dimensions for a node. */
function nodeDims(n: DesignerNode): { w: number; h: number } {
  if (n.type === "anchor") return { w: ANCHOR_WIDTH, h: ANCHOR_HEIGHT };
  if (n.type === "block") {
    return {
      w: (n.style?.width as number) ?? 300,
      h: (n.style?.height as number) ?? 160,
    };
  }
  return { w: NODE_WIDTH, h: NODE_HEIGHT };
}

/**
 * Assign handle IDs to edges based on relative node positions.
 *
 * - Default: ``result`` (right) → ``deps`` (left) for horizontal flow.
 * - When the target is significantly above: use ``result-top`` / ``deps-bottom``
 *   so the bezier curves upward naturally.
 * - When the target is significantly below: use ``result-bottom`` / ``deps-top``
 *   so the bezier curves downward.
 */
function assignHandles(
  nodes: DesignerNode[],
  edges: Edge[],
): Edge[] {
  const nodeMap = new Map<string, DesignerNode>();
  for (const n of nodes) nodeMap.set(n.id, n);

  return edges.map((edge) => {
    const src = nodeMap.get(edge.source);
    const tgt = nodeMap.get(edge.target);
    if (!src || !tgt) {
      return { ...edge, sourceHandle: "result", targetHandle: "deps" };
    }

    const srcDims = nodeDims(src);
    const tgtDims = nodeDims(tgt);
    const srcCenterY = src.position.y + srcDims.h / 2;
    const tgtCenterY = tgt.position.y + tgtDims.h / 2;
    const dy = tgtCenterY - srcCenterY;

    // Only use top/bottom handles for step nodes — anchors/blocks keep default.
    const srcIsStep = src.type === "step";
    const tgtIsStep = tgt.type === "step";

    let sourceHandle = "result";
    let targetHandle = "deps";

    if (srcIsStep && tgtIsStep) {
      const threshold = NODE_HEIGHT;
      if (dy < -threshold) {
        // Target is significantly above — route via top handles
        sourceHandle = "result-top";
        targetHandle = "deps-bottom";
      } else if (dy > threshold) {
        // Target is significantly below — route via bottom handles
        sourceHandle = "result-bottom";
        targetHandle = "deps-top";
      }
    }

    return {
      ...edge,
      id: `${edge.source}:${sourceHandle}->${edge.target}:${targetHandle}`,
      sourceHandle,
      targetHandle,
    };
  });
}
