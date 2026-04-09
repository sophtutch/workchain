// Thin typed wrapper around the designer router endpoints.

import type {
  DraftErrorDetail,
  HandlerDescriptor,
  WorkflowCreatedResponse,
  WorkflowDraft,
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

export { DraftValidationError };
