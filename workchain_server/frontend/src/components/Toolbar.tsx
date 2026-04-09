import type { DraftIssue } from "../lib/draftValidate";

interface ToolbarProps {
  workflowName: string;
  onWorkflowNameChange: (name: string) => void;
  onRun: () => void;
  onClear: () => void;
  running: boolean;
  issues: DraftIssue[];
  statusMessage: string | null;
}

export function Toolbar({
  workflowName,
  onWorkflowNameChange,
  onRun,
  onClear,
  running,
  issues,
  statusMessage,
}: ToolbarProps) {
  return (
    <header className="toolbar">
      <div className="toolbar__title">Workchain Designer</div>
      <div className="toolbar__input-group">
        <label htmlFor="workflow-name" className="sr-only">
          Workflow name
        </label>
        <input
          id="workflow-name"
          className="toolbar__name-input"
          placeholder="Workflow name"
          value={workflowName}
          onChange={(e) => onWorkflowNameChange(e.target.value)}
        />
      </div>
      <div className="toolbar__spacer" />
      {statusMessage && (
        <div className="toolbar__status">{statusMessage}</div>
      )}
      <button
        type="button"
        className="btn btn--ghost"
        onClick={onClear}
        disabled={running}
      >
        Clear
      </button>
      <button
        type="button"
        className="btn btn--primary"
        onClick={onRun}
        disabled={running || issues.length > 0}
        title={
          issues.length > 0
            ? issues.map((i) => i.message).join("\n")
            : "Create and run workflow"
        }
      >
        {running ? "Running…" : "Run"}
      </button>
    </header>
  );
}
