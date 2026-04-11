import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import type { Connection, Edge, ReactFlowInstance } from "reactflow";
import { ReactFlowProvider, applyEdgeChanges, applyNodeChanges, reconnectEdge } from "reactflow";
import { HandlerPalette } from "../components/HandlerPalette";
import { DesignerCanvas } from "../components/DesignerCanvas";
import { ConfigPanel } from "../components/ConfigPanel";
import { Toolbar } from "../components/Toolbar";
import { useHandlers } from "../hooks/useHandlers";
import {
  graphToDraft,
  graphToTemplateSteps,
  isStepNode,
  isBlockAnchor,
  START_NODE_ID,
  END_NODE_ID,
  type DesignerNode,
} from "../lib/graphToDraft";
import { templateToGraph } from "../lib/templateToGraph";
import { draftValidate } from "../lib/draftValidate";
import {
  DraftValidationError,
  createWorkflow,
  fetchTemplate,
  updateTemplate,
  createTemplate,
} from "../api/client";
import type { HandlerDescriptor } from "../api/types";
import { autoLayout } from "../lib/autoLayout";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const INITIAL_NODES: DesignerNode[] = [
  {
    id: START_NODE_ID,
    type: "anchor",
    position: { x: 0, y: 200 },
    data: { label: "START" },
    deletable: false,
    draggable: true,
  },
  {
    id: END_NODE_ID,
    type: "anchor",
    position: { x: 800, y: 200 },
    data: { label: "END" },
    deletable: false,
    draggable: true,
  },
];

const DEFAULT_BLOCK_WIDTH = 300;
const DEFAULT_BLOCK_HEIGHT = 160;
const BLOCK_PADDING = 40; // padding around children inside a block

// ---------------------------------------------------------------------------
// Entry point (wraps with ReactFlowProvider + DesignerProvider)
// ---------------------------------------------------------------------------

export function DesignerPage() {
  return (
    <ReactFlowProvider>
      <DesignerInner />
    </ReactFlowProvider>
  );
}

// ---------------------------------------------------------------------------
// Core designer state
// ---------------------------------------------------------------------------

function DesignerInner() {
  const { handlers, loading, error: handlersError } = useHandlers();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const templateIdParam = searchParams.get("template");

  const [nodes, setNodes] = useState<DesignerNode[]>(INITIAL_NODES);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [canvasKey, setCanvasKey] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [workflowName, setWorkflowName] = useState("");
  const [running, setRunning] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [stepErrorsByNode, setStepErrorsByNode] = useState<Record<string, string[]>>({});
  const [editingTemplate, setEditingTemplate] = useState<{ id: string; version: number } | null>(null);
  const [savingTemplate, setSavingTemplate] = useState(false);
  const templateLoaded = useRef<string | null>(null);
  const nodeCounter = useRef(1);
  const blockCounter = useRef(1);
  const rfInstance = useRef<ReactFlowInstance | null>(null);

  const handlersByName = useMemo(() => {
    const m = new Map<string, HandlerDescriptor>();
    for (const h of handlers) m.set(h.name, h);
    return m;
  }, [handlers]);

  // -----------------------------------------------------------------------
  // Load template from URL query param (?template=id)
  // -----------------------------------------------------------------------

  useEffect(() => {
    if (!templateIdParam || loading || handlers.length === 0) return;
    // Don't reload if we already loaded this template.
    if (templateLoaded.current === templateIdParam) return;

    let cancelled = false;
    (async () => {
      try {
        const template = await fetchTemplate(templateIdParam);
        if (cancelled) return;

        const { nodes: tplNodes, edges: tplEdges } = templateToGraph(template, handlersByName);
        const { nodes: laid, edges: smartEdges } = autoLayout(tplNodes, tplEdges);

        setNodes(laid);
        setEdges(smartEdges);
        setWorkflowName(template.name);
        setEditingTemplate({ id: template.id, version: template.version });
        setSelectedId(null);
        setStatusMessage(null);
        setStepErrorsByNode({});
        nodeCounter.current = template.steps.length + 1;
        blockCounter.current = 1;
        templateLoaded.current = templateIdParam;
        // Bump canvas key to force ReactFlow remount with fitView.
        setCanvasKey((k) => k + 1);
      } catch (err) {
        if (!cancelled) {
          setStatusMessage(
            `Failed to load template: ${err instanceof Error ? err.message : "unknown error"}`,
          );
        }
      }
    })();
    return () => { cancelled = true; };
  }, [templateIdParam, loading, handlers.length, handlersByName]);


  // -----------------------------------------------------------------------
  // React Flow change handlers
  // -----------------------------------------------------------------------

  const onNodesChange = useCallback(
    (changes: Parameters<typeof applyNodeChanges>[0]) =>
      setNodes((ns) => applyNodeChanges(changes, ns) as DesignerNode[]),
    [],
  );
  const onEdgesChange = useCallback(
    (changes: Parameters<typeof applyEdgeChanges>[0]) =>
      setEdges((es) => applyEdgeChanges(changes, es)),
    [],
  );
  const onAddEdge = useCallback(
    (edge: Edge) => setEdges((es) => [...es, edge]),
    [],
  );
  const onUpdateEdge = useCallback(
    (oldEdge: Edge, newConnection: Connection) =>
      setEdges((es) => reconnectEdge(oldEdge, newConnection, es)),
    [],
  );

  // -----------------------------------------------------------------------
  // Block hit-testing: find which block contains a flow-space position
  // -----------------------------------------------------------------------

  const findBlockAtPosition = useCallback(
    (pos: { x: number; y: number }, currentNodes: DesignerNode[]): string | undefined => {
      for (let i = currentNodes.length - 1; i >= 0; i--) {
        const n = currentNodes[i];
        if (n.type !== "block") continue;
        const w = (n.style?.width as number) ?? DEFAULT_BLOCK_WIDTH;
        const h = (n.style?.height as number) ?? DEFAULT_BLOCK_HEIGHT;
        if (
          pos.x >= n.position.x &&
          pos.x <= n.position.x + w &&
          pos.y >= n.position.y &&
          pos.y <= n.position.y + h
        ) {
          return n.id;
        }
      }
      return undefined;
    },
    [],
  );

  // -----------------------------------------------------------------------
  // Auto-resize a block to fit its children + padding
  // -----------------------------------------------------------------------

  const autoResizeBlock = useCallback(
    (blockId: string, ns: DesignerNode[]): DesignerNode[] => {
      const children = ns.filter((n) => n.parentNode === blockId);
      if (children.length === 0) return ns;

      let maxX = 0;
      let maxY = 0;
      for (const c of children) {
        const cw = 160; // approximate step node width
        const ch = 50; // approximate step node height
        maxX = Math.max(maxX, c.position.x + cw);
        maxY = Math.max(maxY, c.position.y + ch);
      }

      // Snap block size to grid so children remain grid-aligned.
      const neededW = Math.round(maxX + BLOCK_PADDING);
      const neededH = Math.round(maxY + BLOCK_PADDING);

      return ns.map((n) => {
        if (n.id !== blockId) return n;
        const curW = (n.style?.width as number) ?? DEFAULT_BLOCK_WIDTH;
        const curH = (n.style?.height as number) ?? DEFAULT_BLOCK_HEIGHT;
        if (neededW <= curW && neededH <= curH) return n;
        return {
          ...n,
          style: {
            ...n.style,
            width: Math.round(Math.max(curW, neededW)),
            height: Math.round(Math.max(curH, neededH)),
          },
        };
      });
    },
    [],
  );

  // -----------------------------------------------------------------------
  // Drop handler onto canvas (or into a block)
  // -----------------------------------------------------------------------

  const onDropHandler = useCallback(
    (handlerName: string, position: { x: number; y: number }) => {
      const descriptor = handlersByName.get(handlerName);
      if (!descriptor || !descriptor.launchable) return;
      const shortName = descriptor.qualname.split(".").pop() ?? "step";
      const stepId = `${shortName}_${nodeCounter.current++}`;
      const projected =
        rfInstance.current?.screenToFlowPosition(position) ?? position;

      setNodes((ns) => {
        const parentBlockId = findBlockAtPosition(projected, ns);
        const block = parentBlockId ? ns.find((n) => n.id === parentBlockId) : null;

        // Snap child position within block to grid for alignment.
        const relPos = block
          ? {
              x: Math.round(projected.x - block.position.x),
              y: Math.round(projected.y - block.position.y),
            }
          : { x: Math.round(projected.x), y: Math.round(projected.y) };

        const stepNode: DesignerNode = {
          id: stepId,
          type: "step",
          position: relPos,
          data: {
            handlerName,
            stepName: stepId,
            configValues: {},
            handlerDescription: descriptor.description ?? undefined,
            handlerIsAsync: descriptor.is_async,
          },
          ...(parentBlockId
            ? { parentNode: parentBlockId, extent: "parent" as const }
            : {}),
        };

        let updated = [...ns, stepNode];
        if (parentBlockId) {
          updated = autoResizeBlock(parentBlockId, updated);
        }
        return updated;
      });
      setSelectedId(stepId);
    },
    [handlersByName, findBlockAtPosition, autoResizeBlock],
  );

  // -----------------------------------------------------------------------
  // Add an empty block
  // -----------------------------------------------------------------------

  // -----------------------------------------------------------------------
  // Node drag stop: re-parenting (drag into/out of blocks)
  // -----------------------------------------------------------------------

  const onNodeDragStop = useCallback(
    (nodeId: string, _position: { x: number; y: number }) => {
      setNodes((ns) => {
        const node = ns.find((n) => n.id === nodeId);
        if (!node || node.type !== "step") return ns;

        // Compute absolute position of the dragged node.
        let absX = node.position.x;
        let absY = node.position.y;
        if (node.parentNode) {
          const parent = ns.find((n) => n.id === node.parentNode);
          if (parent) {
            absX += parent.position.x;
            absY += parent.position.y;
          }
        }

        const currentParent = node.parentNode;
        const newParent = findBlockAtPosition(
          { x: absX, y: absY },
          ns.filter((n) => n.id !== nodeId), // exclude self
        );

        if (currentParent === newParent) return ns; // no change

        // Remove edges that would violate block isolation.
        // (Handled separately in setEdges below.)

        let updated = ns.map((n) => {
          if (n.id !== nodeId) return n;
          if (newParent) {
            const parentNode = ns.find((p) => p.id === newParent);
            return {
              ...n,
              parentNode: newParent,
              extent: "parent" as const,
              position: {
                x: Math.round(absX - (parentNode?.position.x ?? 0)),
                y: Math.round(absY - (parentNode?.position.y ?? 0)),
              },
            };
          }
          // Moved out of block — remove parentNode.
          const { parentNode: _removed, extent: _ext, ...rest } = n;
          return { ...rest, position: { x: Math.round(absX), y: Math.round(absY) } } as DesignerNode;
        });

        if (newParent) {
          updated = autoResizeBlock(newParent, updated);
        }
        return updated;
      });

    },
    [findBlockAtPosition, autoResizeBlock],
  );

  // -----------------------------------------------------------------------
  // Step and block mutations
  // -----------------------------------------------------------------------

  const onStepNameChange = useCallback((nodeId: string, name: string) => {
    setNodes((ns) =>
      ns.map((n) =>
        n.id === nodeId && isStepNode(n)
          ? { ...n, data: { ...n.data, stepName: name } }
          : n,
      ),
    );
  }, []);

  const onConfigChange = useCallback(
    (nodeId: string, values: Record<string, unknown>) => {
      setNodes((ns) =>
        ns.map((n) =>
          n.id === nodeId && isStepNode(n)
            ? { ...n, data: { ...n.data, configValues: values } }
            : n,
        ),
      );
    },
    [],
  );

  const onBlockLabelChange = useCallback((nodeId: string, label: string) => {
    setNodes((ns) =>
      ns.map((n) =>
        n.id === nodeId && n.type === "block"
          ? { ...n, data: { ...n.data, label } }
          : n,
      ),
    );
  }, []);

  const onUnparent = useCallback((nodeId: string) => {
    setNodes((ns) => {
      const node = ns.find((n) => n.id === nodeId);
      if (!node || !node.parentNode) return ns;
      const parent = ns.find((n) => n.id === node.parentNode);
      const absX = node.position.x + (parent?.position.x ?? 0);
      const absY = node.position.y + (parent?.position.y ?? 0);
      return ns.map((n) => {
        if (n.id !== nodeId) return n;
        const { parentNode: _p, extent: _e, ...rest } = n;
        return { ...rest, position: { x: absX, y: absY } } as DesignerNode;
      });
    });
  }, []);

  const onDelete = useCallback((nodeId: string) => {
    // Don't allow deleting block-internal S/E anchors directly.
    if (isBlockAnchor(nodeId)) return;

    // Collect all IDs to remove (node + children if block).
    const idsToRemove = new Set([nodeId]);
    setNodes((ns) => {
      const target = ns.find((n) => n.id === nodeId);
      if (!target) return ns;
      if (target.type === "block") {
        for (const n of ns) {
          if (n.parentNode === nodeId) idsToRemove.add(n.id);
        }
      }
      return ns.filter((n) => !idsToRemove.has(n.id));
    });
    // Remove edges connected to any deleted node (including block children).
    setEdges((es) =>
      es.filter((e) => !idsToRemove.has(e.source) && !idsToRemove.has(e.target)),
    );
    setSelectedId((id) => (id === nodeId ? null : id));
  }, []);

  const onClear = useCallback(() => {
    setNodes(INITIAL_NODES);
    setEdges([]);
    setSelectedId(null);
    setStatusMessage(null);
    setStepErrorsByNode({});
    setEditingTemplate(null);
    templateLoaded.current = null;
    nodeCounter.current = 1;
    blockCounter.current = 1;
    // Clear template query param from URL.
    if (templateIdParam) {
      navigate("/designer", { replace: true });
    }
  }, [templateIdParam, navigate]);

  // -----------------------------------------------------------------------
  // Save template (overwrite existing)
  // -----------------------------------------------------------------------

  const onSaveTemplate = useCallback(async () => {
    if (!editingTemplate) return;
    setSavingTemplate(true);
    setStatusMessage(null);
    try {
      const steps = graphToTemplateSteps(nodes, edges);
      const updated = await updateTemplate(editingTemplate.id, {
        expected_version: editingTemplate.version,
        name: workflowName,
        steps,
      });
      setEditingTemplate({ id: updated.id, version: updated.version });
      setStatusMessage("Template saved");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Save failed";
      if (msg.includes("409") || msg.includes("version mismatch")) {
        setStatusMessage("Template was modified elsewhere — reload and try again");
      } else {
        setStatusMessage(msg);
      }
    } finally {
      setSavingTemplate(false);
    }
  }, [editingTemplate, nodes, edges, workflowName]);

  // -----------------------------------------------------------------------
  // Save as new template
  // -----------------------------------------------------------------------

  const onSaveAsNewTemplate = useCallback(async () => {
    setSavingTemplate(true);
    setStatusMessage(null);
    try {
      const steps = graphToTemplateSteps(nodes, edges);
      const name = editingTemplate
        ? `${workflowName} (copy)`
        : workflowName;
      const created = await createTemplate({ name, steps });
      setEditingTemplate({ id: created.id, version: created.version });
      setWorkflowName(created.name);
      templateLoaded.current = created.id;
      setStatusMessage(`Saved as new template '${created.name}'`);
      // Update URL to reflect new template.
      navigate(`/designer?template=${encodeURIComponent(created.id)}`, { replace: true });
    } catch (err) {
      setStatusMessage(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSavingTemplate(false);
    }
  }, [editingTemplate, nodes, edges, workflowName, navigate]);

  // -----------------------------------------------------------------------
  // Validation + run
  // -----------------------------------------------------------------------

  const issues = useMemo(
    () => draftValidate(workflowName, nodes, edges),
    [workflowName, nodes, edges],
  );

  const onRun = useCallback(async () => {
    if (issues.length > 0) return;
    setRunning(true);
    setStatusMessage(null);
    setStepErrorsByNode({});
    try {
      const draft = graphToDraft(workflowName, nodes, edges);
      const result = await createWorkflow(draft);
      setStatusMessage(`Launched '${result.name}' (${result.id.slice(0, 8)}…)`);
    } catch (err) {
      if (err instanceof DraftValidationError) {
        const sNodes = nodes.filter(isStepNode);
        const nameToId = new Map(sNodes.map((n) => [n.data.stepName, n.id]));
        const mapped: Record<string, string[]> = {};
        for (const e of err.detail.errors) {
          const id = nameToId.get(e.step);
          if (!id) continue;
          const fieldLines =
            e.field_errors?.map((fe) => `${fe.loc.join(".")}: ${fe.msg}`) ?? [];
          mapped[id] = [e.error, ...fieldLines];
        }
        setStepErrorsByNode(mapped);
        setStatusMessage(err.detail.detail);
      } else {
        setStatusMessage(
          err instanceof Error ? err.message : "Failed to create workflow",
        );
      }
    } finally {
      setRunning(false);
    }
  }, [issues, workflowName, nodes, edges]);

  // -----------------------------------------------------------------------
  // Selected node resolution
  // -----------------------------------------------------------------------


  const selectedNode = nodes.find((n) => n.id === selectedId) ?? null;
  // Only provide handler for step nodes.
  const selectedStepNode = selectedNode && isStepNode(selectedNode) ? selectedNode : null;
  const selectedHandler = selectedStepNode
    ? handlersByName.get(selectedStepNode.data.handlerName) ?? null
    : null;
  const selectedErrors = selectedId ? stepErrorsByNode[selectedId] ?? [] : [];

  return (
    <>
      <div className="designer">
        <Toolbar
          workflowName={workflowName}
          onWorkflowNameChange={setWorkflowName}
          onRun={onRun}
          onClear={onClear}
          running={running}
          issues={issues}
          statusMessage={statusMessage}
          editingTemplateId={editingTemplate?.id ?? null}
          onSaveTemplate={onSaveTemplate}
          onSaveAsNewTemplate={onSaveAsNewTemplate}
          savingTemplate={savingTemplate}
        />
        <div className="designer__body">
          <HandlerPalette
            handlers={handlers}
            loading={loading}
            error={handlersError}
          />
          <DesignerCanvas
            key={canvasKey}
            nodes={nodes}
            edges={edges}
            handlers={handlers}
            selectedId={selectedId}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onAddEdge={onAddEdge}
            onUpdateEdge={onUpdateEdge}
            onDropHandler={onDropHandler}
            onSelect={setSelectedId}
            onNodeDragStop={onNodeDragStop}
            setReactFlowInstance={(inst) => {
              rfInstance.current = inst;
              // Always fit on init — covers both fresh canvas and template load.
              inst.fitView({ padding: 0.1 });
            }}
          />
          <ConfigPanel
            selectedNode={selectedNode}
            handler={selectedHandler}
            onStepNameChange={onStepNameChange}
            onConfigChange={onConfigChange}
            onBlockLabelChange={onBlockLabelChange}
            onDelete={onDelete}
            onUnparent={onUnparent}
            errors={selectedErrors}
          />
        </div>
      </div>
    </>
  );
}
