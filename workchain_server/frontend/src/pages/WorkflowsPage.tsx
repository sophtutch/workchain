import { List } from "lucide-react";
import { useWorkflowSearch } from "../hooks/useWorkflowSearch";
import { WorkflowFilters } from "../components/WorkflowFilters";
import { WorkflowTable } from "../components/WorkflowTable";
import { Pagination } from "../components/Pagination";

export function WorkflowsPage() {
  const {
    workflows,
    total,
    loading,
    page,
    pageSize,
    status,
    search,
    setStatus,
    setSearch,
    setPage,
    toast,
  } = useWorkflowSearch();

  return (
    <div className="dashboard">
      <h2 className="dashboard__heading">
        <List size={16} /> Workflows
      </h2>

      <WorkflowFilters
        status={status}
        search={search}
        onStatusChange={setStatus}
        onSearchChange={setSearch}
      />

      <WorkflowTable
        workflows={workflows}
        loading={loading}
      />

      <Pagination
        page={page}
        pageSize={pageSize}
        total={total}
        onPageChange={setPage}
      />

      {/* Toast */}
      <div className={`toast ${toast ? "toast--visible" : ""}`}>
        {toast}
      </div>
    </div>
  );
}
