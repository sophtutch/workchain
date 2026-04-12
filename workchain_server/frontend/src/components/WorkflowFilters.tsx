import { useState } from "react";
import { Search, X } from "lucide-react";

const STATUSES = [
  { key: "pending",       label: "Pending" },
  { key: "running",       label: "Running" },
  { key: "completed",     label: "Completed" },
  { key: "failed",        label: "Failed" },
  { key: "needs_review",  label: "Review" },
  { key: "cancelled",     label: "Cancelled" },
];

interface WorkflowFiltersProps {
  status: string;
  search: string;
  onStatusChange: (status: string) => void;
  onSearchChange: (search: string) => void;
}

export function WorkflowFilters({
  status,
  search,
  onStatusChange,
  onSearchChange,
}: WorkflowFiltersProps) {
  const [inputValue, setInputValue] = useState(search);

  return (
    <div className="wf-filters">
      <div className="wf-filters__search">
        <Search size={14} className="wf-filters__search-icon" />
        <input
          type="text"
          placeholder="Search workflows..."
          value={inputValue}
          onChange={(e) => {
            setInputValue(e.target.value);
            onSearchChange(e.target.value);
          }}
          className="wf-filters__input"
        />
        {inputValue && (
          <button
            className="wf-filters__clear"
            onClick={() => {
              setInputValue("");
              onSearchChange("");
            }}
          >
            <X size={12} />
          </button>
        )}
      </div>
      <div className="wf-filters__pills">
        <button
          className={`wf-filters__pill ${!status ? "wf-filters__pill--active" : ""}`}
          onClick={() => onStatusChange("")}
        >
          All
        </button>
        {STATUSES.map((s) => (
          <button
            key={s.key}
            className={`wf-filters__pill wf-filters__pill--${s.key} ${status === s.key ? "wf-filters__pill--active" : ""}`}
            onClick={() => onStatusChange(status === s.key ? "" : s.key)}
          >
            {s.label}
          </button>
        ))}
      </div>
    </div>
  );
}
