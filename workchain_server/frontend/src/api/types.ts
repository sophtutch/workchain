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
