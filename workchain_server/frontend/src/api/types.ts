// Mirrors the Pydantic models exposed by workchain_server/designer_router.py.
// Keep field names in lockstep with HandlerDescriptor and WorkflowDraft.

export interface HandlerDescriptor {
  name: string;
  module: string;
  qualname: string;
  doc: string | null;
  description: string | null;
  category: string | null;
  is_async: boolean;
  is_completeness_check: boolean;
  needs_context: boolean;
  idempotent: boolean;
  config_type: string | null;
  config_schema: Record<string, unknown> | null;
  result_type: string | null;
  result_schema: Record<string, unknown> | null;
  retry_policy: Record<string, unknown> | null;
  poll_policy: Record<string, unknown> | null;
  completeness_check: string | null;
  depends_on: string[] | null;
  launchable: boolean;
  introspection_warning: string | null;
}

export interface StepDraft {
  name: string;
  handler: string;
  config: Record<string, unknown> | null;
  depends_on: string[] | null;
  retry_policy?: Record<string, unknown> | null;
  poll_policy?: Record<string, unknown> | null;
  step_timeout?: number;
}

export interface WorkflowDraft {
  name: string;
  steps: StepDraft[];
}

export interface DraftStepError {
  step: string;
  error: string;
  field_errors: Array<{ loc: Array<string | number>; msg: string; type: string }> | null;
}

export interface DraftErrorDetail {
  detail: string;
  errors: DraftStepError[];
}

export interface WorkflowCreatedResponse {
  id: string;
  name: string;
  status: string;
}

export interface ServerConfig {
  server_title: string;
  instance_id: string;
}

export interface WorkflowSummary {
  id: string;
  name: string;
  status: string;
  progress: string;
  total_steps: number;
  completed_steps: number;
  created_at: string;
  updated_at: string;
}

export interface WorkflowStats {
  pending: number;
  running: number;
  completed: number;
  failed: number;
  needs_review: number;
  cancelled: number;
}

export interface TemplateStep {
  name: string;
  handler: string;
  config: Record<string, unknown> | null;
  depends_on: string[] | null;
  retry_policy?: Record<string, unknown> | null;
  poll_policy?: Record<string, unknown> | null;
  step_timeout?: number;
}

export interface WorkflowTemplate {
  id: string;
  name: string;
  description: string | null;
  steps: TemplateStep[];
  version: number;
  created_at: string;
  updated_at: string;
}

export interface WorkflowListResponse {
  items: WorkflowSummary[];
  total: number;
}

export interface WorkflowAnalytics {
  total_workflows: number;
  success_rate: number | null;
  status_counts: WorkflowStats;
  avg_duration_seconds: number | null;
  recent_completions_24h: number;
  recent_failures_24h: number;
  throughput_24h: number;
}

export interface ActivityItem {
  id: string;
  name: string;
  status: string;
  updated_at: string;
  created_at: string;
}

export interface WorkflowSearchParams {
  status?: string;
  search?: string;
  limit?: number;
  skip?: number;
}

export interface StepDetail {
  name: string;
  handler: string;
  status: string;
  attempt: number;
  is_async: boolean;
  depends_on: string[];
  step_timeout: number;
  config: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  retry_policy: {
    max_attempts: number;
    wait_seconds: number;
    wait_multiplier: number;
    wait_max: number;
  };
  poll_policy: {
    interval: number;
    backoff_multiplier: number;
    max_interval: number;
    timeout: number;
    max_polls: number;
  } | null;
  poll_count: number;
  last_poll_progress: number | null;
  last_poll_message: string | null;
  locked_by: string | null;
  fence_token: number;
}

export interface AuditEvent {
  event_type: string;
  timestamp: string;
  sequence: number;
  step_name: string | null;
  step_status: string | null;
  step_status_before: string | null;
  instance_id: string | null;
  attempt: number | null;
  error: string | null;
  error_traceback: string | null;
  poll_count: number | null;
  poll_progress: number | null;
  poll_message: string | null;
  recovery_action: string | null;
  result_summary: Record<string, unknown> | null;
}

export interface WorkflowDetailResponse {
  workflow: {
    id: string;
    name: string;
    status: string;
    created_at: string;
    updated_at: string;
  };
  steps: StepDetail[];
  events: AuditEvent[];
  graph: {
    dependencies: Record<string, string[]>;
    tiers: string[][];
  };
}

export interface TemplateUpdateBody {
  expected_version: number;
  name?: string;
  description?: string;
  steps?: TemplateStep[];
}

export interface TemplateCreateBody {
  name: string;
  description?: string;
  steps: TemplateStep[];
}
