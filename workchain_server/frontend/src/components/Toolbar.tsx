import { Play, Trash2, Save, Copy } from "lucide-react";
import type { DraftIssue } from "../lib/draftValidate";

interface ToolbarProps {
  workflowName: string;
  onWorkflowNameChange: (name: string) => void;
  onRun: () => void;
  onClear: () => void;
  running: boolean;
  issues: DraftIssue[];
  statusMessage: string | null;
  editingTemplateId: string | null;
  onSaveTemplate: () => void;
  onSaveAsNewTemplate: () => void;
  savingTemplate: boolean;
}

export function Toolbar({
  workflowName,
  onWorkflowNameChange,
  onRun,
  onClear,
  running,
  issues,
  statusMessage,
  editingTemplateId,
  onSaveTemplate,
  onSaveAsNewTemplate,
  savingTemplate,
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
        disabled={running || savingTemplate}
      >
        <Trash2 size={14} /> Clear
      </button>
      {editingTemplateId && (
        <button
          type="button"
          className="btn btn--ghost"
          onClick={onSaveTemplate}
          disabled={savingTemplate || !workflowName.trim()}
          title="Save changes to this template"
        >
          <Save size={14} /> {savingTemplate ? "Saving…" : "Save"}
        </button>
      )}
      <button
        type="button"
        className="btn btn--ghost"
        onClick={onSaveAsNewTemplate}
        disabled={savingTemplate || !workflowName.trim()}
        title="Save as a new template"
      >
        <Copy size={14} /> Save As New
      </button>
      <button
        type="button"
        className="btn btn--primary"
        onClick={onRun}
        disabled={running || savingTemplate || issues.length > 0}
        title={
          issues.length > 0
            ? issues.map((i) => i.message).join("\n")
            : "Create and run workflow"
        }
      >
        <Play size={14} /> {running ? "Running…" : "Run"}
      </button>
    </header>
  );
}
