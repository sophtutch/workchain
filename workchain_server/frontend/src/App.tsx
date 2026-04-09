import { useCallback, useMemo, useRef, useState } from "react";
import type { Edge, ReactFlowInstance } from "reactflow";
import { ReactFlowProvider, applyEdgeChanges, applyNodeChanges } from "reactflow";
import { HandlerPalette } from "./components/HandlerPalette";
import { DesignerCanvas } from "./components/DesignerCanvas";
import { ConfigPanel } from "./components/ConfigPanel";
import { Toolbar } from "./components/Toolbar";
import { useHandlers } from "./hooks/useHandlers";
import { graphToDraft, type StepNode } from "./lib/graphToDraft";
import { draftValidate } from "./lib/draftValidate";
import { DraftValidationError, createWorkflow } from "./api/client";
import type { HandlerDescriptor } from "./api/types";

export function App() {
  return (
    <ReactFlowProvider>
      <AppInner />
    </ReactFlowProvider>
  );
}

function AppInner() {
  const { handlers, loading, error: handlersError } = useHandlers();
  const [nodes, setNodes] = useState<StepNode[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [workflowName, setWorkflowName] = useState("");
  const [running, setRunning] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [stepErrorsByNode, setStepErrorsByNode] = useState<Record<string, string[]>>({});
  const nodeCounter = useRef(1);
  const rfInstance = useRef<ReactFlowInstance | null>(null);

  const handlersByName = useMemo(() => {
    const m = new Map<string, HandlerDescriptor>();
    for (const h of handlers) m.set(h.name, h);
    return m;
  }, [handlers]);

  const onNodesChange = useCallback(
    (changes: Parameters<typeof applyNodeChanges>[0]) =>
      setNodes((ns) => applyNodeChanges(changes, ns) as StepNode[]),
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

  const onDropHandler = useCallback(
    (handlerName: string, position: { x: number; y: number }) => {
      const descriptor = handlersByName.get(handlerName);
      if (!descriptor || !descriptor.launchable) return;
      const shortName = descriptor.qualname.split(".").pop() ?? "step";
      const id = `${shortName}_${nodeCounter.current++}`;
      // Convert screen coordinates to React Flow's internal coordinate
      // system so dropped nodes land under the cursor.
      const projected = rfInstance.current?.project(position) ?? position;
      const newNode: StepNode = {
        id,
        type: "step",
        position: projected,
        data: {
          handlerName,
          stepName: id,
          configValues: {},
        },
      };
      setNodes((ns) => [...ns, newNode]);
      setSelectedId(id);
    },
    [handlersByName],
  );

  const onStepNameChange = useCallback((nodeId: string, name: string) => {
    setNodes((ns) =>
      ns.map((n) =>
        n.id === nodeId ? { ...n, data: { ...n.data, stepName: name } } : n,
      ),
    );
  }, []);

  const onConfigChange = useCallback(
    (nodeId: string, values: Record<string, unknown>) => {
      setNodes((ns) =>
        ns.map((n) =>
          n.id === nodeId
            ? { ...n, data: { ...n.data, configValues: values } }
            : n,
        ),
      );
    },
    [],
  );

  const onDelete = useCallback((nodeId: string) => {
    setNodes((ns) => ns.filter((n) => n.id !== nodeId));
    setEdges((es) => es.filter((e) => e.source !== nodeId && e.target !== nodeId));
    setSelectedId((id) => (id === nodeId ? null : id));
  }, []);

  const onClear = useCallback(() => {
    setNodes([]);
    setEdges([]);
    setSelectedId(null);
    setStatusMessage(null);
    setStepErrorsByNode({});
    nodeCounter.current = 1;
  }, []);

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
        // Map the backend's per-step errors back to node ids by stepName.
        const nameToId = new Map(nodes.map((n) => [n.data.stepName, n.id]));
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

  const selectedNode = nodes.find((n) => n.id === selectedId) ?? null;
  const selectedHandler = selectedNode
    ? handlersByName.get(selectedNode.data.handlerName) ?? null
    : null;
  const selectedErrors = selectedId ? stepErrorsByNode[selectedId] ?? [] : [];

  return (
    <div className="app">
      <Toolbar
        workflowName={workflowName}
        onWorkflowNameChange={setWorkflowName}
        onRun={onRun}
        onClear={onClear}
        running={running}
        issues={issues}
        statusMessage={statusMessage}
      />
      <div className="app__body">
        <HandlerPalette
          handlers={handlers}
          loading={loading}
          error={handlersError}
        />
        <DesignerCanvas
          nodes={nodes}
          edges={edges}
          handlers={handlers}
          selectedId={selectedId}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onAddEdge={onAddEdge}
          onDropHandler={onDropHandler}
          onSelect={setSelectedId}
          setReactFlowInstance={(inst) => {
            rfInstance.current = inst;
          }}
        />
        <ConfigPanel
          node={selectedNode}
          handler={selectedHandler}
          onStepNameChange={onStepNameChange}
          onConfigChange={onConfigChange}
          onDelete={onDelete}
          errors={selectedErrors}
        />
      </div>
    </div>
  );
}
