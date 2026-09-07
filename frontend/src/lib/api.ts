/**
 * API client for the frozen contract.
 *
 * The investigate endpoint streams NDJSON over a POST body. EventSource cannot
 * issue a POST, so this reads the response with fetch + a ReadableStream reader
 * and splits on newlines. Partial lines are carried across chunks — a JSON
 * object split across two network chunks is the normal case, not an edge case.
 */

import type {
  AttributionResponse,
  CaseCreateRequest,
  CaseSummary,
  ClustersResponse,
  FindingsResponse,
  GraphResponse,
  HealthResponse,
  ProgressEvent,
  ReportResponse,
  ReviewRequest,
  ReviewResponse,
  VerifyResponse,
} from '../contracts/api';

/** Errors the backend raised deliberately, with a message written to be read. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: Record<string, unknown>;

  constructor(status: number, code: string, message: string, detail = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

const OFFLINE_MESSAGE =
  'Cannot reach the investigation service. Start it with: ' +
  'py -3.11 -m uvicorn backend.api.main:app --port 8000';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, 'service_unreachable', OFFLINE_MESSAGE);
  }

  if (!response.ok) {
    let code = 'request_failed';
    let message = `The service returned ${response.status}.`;
    let detail: Record<string, unknown> = {};
    try {
      const body = await response.json();
      code = body.error ?? code;
      message = body.message ?? body.detail ?? message;
      detail = body.detail ?? {};
      if (typeof message !== 'string') message = JSON.stringify(message);
    } catch {
      /* Body was not JSON; the status-based message above still reads. */
    }
    throw new ApiError(response.status, code, message, detail);
  }

  return (await response.json()) as T;
}

export const api = {
  health: () => request<HealthResponse>('/api/health'),

  createCase: (body: CaseCreateRequest) =>
    request<CaseSummary>('/api/cases', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  getCase: (id: string) => request<CaseSummary>(`/api/cases/${id}`),

  getGraph: (id: string, collapsed: boolean) =>
    request<GraphResponse>(`/api/cases/${id}/graph?collapsed=${collapsed}`),

  getClusters: (id: string) => request<ClustersResponse>(`/api/cases/${id}/clusters`),

  getFindings: (id: string) => request<FindingsResponse>(`/api/cases/${id}/findings`),

  reviewFinding: (id: string, findingId: string, body: ReviewRequest) =>
    request<ReviewResponse>(`/api/cases/${id}/findings/${findingId}/review`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  getAttribution: (id: string) =>
    request<AttributionResponse>(`/api/cases/${id}/attribution`),

  createReport: (id: string, generatedBy: string) =>
    request<ReportResponse>(`/api/cases/${id}/report`, {
      method: 'POST',
      body: JSON.stringify({ generated_by: generatedBy }),
    }),

  verifyEvidence: (id: string, evidenceId: string) =>
    request<VerifyResponse>(`/api/cases/${id}/evidence/${evidenceId}/verify`),
};

/**
 * Run the pipeline, calling `onEvent` for each progress line as it arrives.
 *
 * Resolves when the stream closes. Never throws for a pipeline-level failure:
 * the backend emits an `error` event and the caller renders it, because a raw
 * exception on screen is exactly what the honest-degradation rule forbids.
 */
export async function investigate(
  caseId: string,
  onEvent: (event: ProgressEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`/api/cases/${caseId}/investigate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal,
    });
  } catch (error) {
    if ((error as Error)?.name === 'AbortError') return;
    throw new ApiError(0, 'service_unreachable', OFFLINE_MESSAGE);
  }

  if (!response.ok || !response.body) {
    throw new ApiError(
      response.status,
      'investigation_failed',
      `The investigation could not be started (HTTP ${response.status}).`,
    );
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  const emit = (line: string) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    try {
      onEvent(JSON.parse(trimmed) as ProgressEvent);
    } catch {
      /* A malformed line is skipped rather than killing the whole stream. */
    }
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // A JSON object can be split across chunks; keep the tail for next time.
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      lines.forEach(emit);
    }
    buffer += decoder.decode();
    emit(buffer);
  } finally {
    reader.releaseLock();
  }
}
