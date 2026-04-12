// Thin typed wrapper around the designer router endpoints.

import type {
  ActivityItem,
  DraftErrorDetail,
  HandlerDescriptor,
  ServerConfig,
  TemplateCreateBody,
  TemplateUpdateBody,
  WorkflowAnalytics,
  WorkflowCreatedResponse,
  WorkflowDetailResponse,
  WorkflowDraft,
  WorkflowListResponse,
  WorkflowSearchParams,
  WorkflowStats,
  WorkflowTemplate,
} from "./types";

const API_BASE = "/api/v1";

class DraftValidationError extends Error {
  detail: DraftErrorDetail;
  constructor(detail: DraftErrorDetail) {
    super(detail.detail);
    this.name = "DraftValidationError";
    this.detail = detail;
  }
}

async function handleResponse<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let body: unknown = null;
    try {
      body = await resp.json();
    } catch {
      /* non-JSON error body */
    }
    // FastAPI wraps HTTPException(detail=X) as { detail: X }.
    const detail =
      body && typeof body === "object" && "detail" in body
        ? (body as { detail: unknown }).detail
        : null;
    if (
      detail &&
      typeof detail === "object" &&
      "errors" in (detail as Record<string, unknown>)
    ) {
      throw new DraftValidationError(detail as DraftErrorDetail);
    }
    throw new Error(
      `API ${resp.status}: ${
        typeof detail === "string" ? detail : resp.statusText
      }`,
    );
  }
  return (await resp.json()) as T;
}

export async function fetchHandlers(): Promise<HandlerDescriptor[]> {
  const resp = await fetch(`${API_BASE}/handlers`);
  return handleResponse<HandlerDescriptor[]>(resp);
}

export async function createWorkflow(
  draft: WorkflowDraft,
): Promise<WorkflowCreatedResponse> {
  const resp = await fetch(`${API_BASE}/workflows`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(draft),
  });
  return handleResponse<WorkflowCreatedResponse>(resp);
}

export async function fetchConfig(): Promise<ServerConfig> {
  const resp = await fetch(`${API_BASE}/config`);
  return handleResponse<ServerConfig>(resp);
}

export async function fetchWorkflows(
  params?: WorkflowSearchParams,
): Promise<WorkflowListResponse> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set("status", params.status);
  if (params?.search) qs.set("search", params.search);
  if (params?.limit != null) qs.set("limit", String(params.limit));
  if (params?.skip != null) qs.set("skip", String(params.skip));
  const query = qs.toString();
  const url = `${API_BASE}/workflows${query ? `?${query}` : ""}`;
  const resp = await fetch(url);
  return handleResponse<WorkflowListResponse>(resp);
}

export async function fetchAnalytics(): Promise<WorkflowAnalytics> {
  const resp = await fetch(`${API_BASE}/workflows/analytics`);
  return handleResponse<WorkflowAnalytics>(resp);
}

export async function fetchActivity(
  limit = 10,
): Promise<ActivityItem[]> {
  const resp = await fetch(`${API_BASE}/workflows/activity?limit=${limit}`);
  return handleResponse<ActivityItem[]>(resp);
}

export async function fetchStats(): Promise<WorkflowStats> {
  const resp = await fetch(`${API_BASE}/workflows/stats`);
  return handleResponse<WorkflowStats>(resp);
}

export async function fetchWorkflowDetail(
  id: string,
): Promise<WorkflowDetailResponse> {
  const resp = await fetch(
    `${API_BASE}/workflows/${encodeURIComponent(id)}/detail`,
  );
  return handleResponse<WorkflowDetailResponse>(resp);
}

export async function cancelWorkflow(id: string): Promise<void> {
  const resp = await fetch(`${API_BASE}/workflows/${encodeURIComponent(id)}/cancel`, {
    method: "POST",
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Cancel failed: ${text}`);
  }
}

export async function retryStep(
  workflowId: string,
  stepName: string,
): Promise<void> {
  const resp = await fetch(
    `${API_BASE}/workflows/${encodeURIComponent(workflowId)}/steps/${encodeURIComponent(stepName)}/retry`,
    { method: "POST" },
  );
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Retry failed: ${text}`);
  }
}

export async function fetchTemplates(): Promise<WorkflowTemplate[]> {
  const resp = await fetch(`${API_BASE}/templates`);
  return handleResponse<WorkflowTemplate[]>(resp);
}

export async function fetchTemplate(id: string): Promise<WorkflowTemplate> {
  const resp = await fetch(`${API_BASE}/templates/${encodeURIComponent(id)}`);
  return handleResponse<WorkflowTemplate>(resp);
}

export async function updateTemplate(
  id: string,
  body: TemplateUpdateBody,
): Promise<WorkflowTemplate> {
  const resp = await fetch(`${API_BASE}/templates/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return handleResponse<WorkflowTemplate>(resp);
}

export async function createTemplate(
  body: TemplateCreateBody,
): Promise<WorkflowTemplate> {
  const resp = await fetch(`${API_BASE}/templates`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return handleResponse<WorkflowTemplate>(resp);
}

export async function launchTemplate(
  templateId: string,
  nameOverride?: string,
  configOverrides?: Record<string, Record<string, unknown>>,
): Promise<WorkflowCreatedResponse> {
  const body: Record<string, unknown> = {};
  if (nameOverride) body.name_override = nameOverride;
  if (configOverrides && Object.keys(configOverrides).length > 0) {
    body.config_overrides = configOverrides;
  }
  const resp = await fetch(
    `${API_BASE}/templates/${encodeURIComponent(templateId)}/launch`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  return handleResponse<WorkflowCreatedResponse>(resp);
}

export async function deleteTemplate(id: string): Promise<void> {
  const resp = await fetch(`${API_BASE}/templates/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Delete failed: ${text}`);
  }
}

export { DraftValidationError };
