/**
 * Case state.
 *
 * One hook owns the whole investigation lifecycle so the screens stay
 * presentational. Progress events arrive from the NDJSON stream and update the
 * counters live; every panel refetches from the API once the stream completes,
 * so nothing on screen is derived from a guess about what the pipeline did.
 */

import { useCallback, useRef, useState } from 'react';
import { ApiError, api, investigate } from './api';
import type {
  AttributionResponse,
  CaseCounts,
  CaseSummary,
  Chain,
  ClustersResponse,
  FindingsResponse,
  GraphResponse,
  ProgressEvent,
  ReportResponse,
  VerifyResponse,
} from '../contracts/api';

export interface CaseData {
  summary: CaseSummary | null;
  graphCollapsed: GraphResponse | null;
  graphExpanded: GraphResponse | null;
  clusters: ClustersResponse | null;
  findings: FindingsResponse | null;
  attribution: AttributionResponse | null;
  report: ReportResponse | null;
  verification: VerifyResponse | null;
}

const EMPTY: CaseData = {
  summary: null,
  graphCollapsed: null,
  graphExpanded: null,
  clusters: null,
  findings: null,
  attribution: null,
  report: null,
  verification: null,
};

export type Phase = 'idle' | 'creating' | 'running' | 'loading' | 'ready' | 'failed';

export function useCase() {
  const [data, setData] = useState<CaseData>(EMPTY);
  const [phase, setPhase] = useState<Phase>('idle');
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [liveCounts, setLiveCounts] = useState<CaseCounts | null>(null);
  const [percent, setPercent] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [elapsedMs, setElapsedMs] = useState<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const loadAll = useCallback(async (caseId: string) => {
    const [summary, graphCollapsed, graphExpanded, clusters, findings, attribution] =
      await Promise.all([
        api.getCase(caseId),
        api.getGraph(caseId, true),
        api.getGraph(caseId, false),
        api.getClusters(caseId),
        api.getFindings(caseId),
        api.getAttribution(caseId),
      ]);
    setData((current) => ({
      ...current,
      summary,
      graphCollapsed,
      graphExpanded,
      clusters,
      findings,
      attribution,
    }));
    return summary;
  }, []);

  /** Open a case and immediately run the pipeline, streaming progress. */
  const start = useCallback(
    async (input: { complaint_id: string; wallet_address: string; chain: Chain }) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      setData(EMPTY);
      setEvents([]);
      setLiveCounts(null);
      setPercent(0);
      setError(null);
      setElapsedMs(null);
      setPhase('creating');

      const startedAt = performance.now();

      try {
        const created = await api.createCase(input);
        setData((current) => ({ ...current, summary: created }));
        setPhase('running');

        let streamFailed: string | null = null;

        await investigate(
          created.id,
          (event) => {
            setEvents((current) => [...current, event]);
            setPercent(event.percent);
            if (event.counts) setLiveCounts(event.counts);
            if (event.event === 'error') streamFailed = event.message;
          },
          controller.signal,
        );

        if (controller.signal.aborted) return null;

        setElapsedMs(performance.now() - startedAt);
        setPhase('loading');
        const summary = await loadAll(created.id);

        if (streamFailed) {
          // Partial results are still shown; the failure is stated, not hidden.
          setError(streamFailed);
        }
        setPhase(streamFailed ? 'failed' : 'ready');
        return summary;
      } catch (caught) {
        if (controller.signal.aborted) return null;
        setError(
          caught instanceof ApiError
            ? caught.message
            : 'The investigation could not be completed.',
        );
        setPhase('failed');
        return null;
      }
    },
    [loadAll],
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  const reset = useCallback(() => {
    cancel();
    setData(EMPTY);
    setEvents([]);
    setLiveCounts(null);
    setPercent(0);
    setError(null);
    setElapsedMs(null);
    setPhase('idle');
  }, [cancel]);

  /** Confirm or reject a finding. The backend recomputes the score. */
  const review = useCallback(
    async (findingId: string, action: 'confirm' | 'reject', note?: string) => {
      const caseId = data.summary?.id;
      if (!caseId) return;
      try {
        await api.reviewFinding(caseId, findingId, {
          action,
          note: note?.trim() ? note.trim() : null,
        });
        // Re-fetch rather than patching locally: the score moved server-side and
        // the case summary is the authority on what it is now.
        const [summary, findings] = await Promise.all([
          api.getCase(caseId),
          api.getFindings(caseId),
        ]);
        setData((current) => ({ ...current, summary, findings }));
      } catch (caught) {
        setError(
          caught instanceof ApiError ? caught.message : 'The review could not be saved.',
        );
      }
    },
    [data.summary?.id],
  );

  const generateReport = useCallback(
    async (generatedBy: string) => {
      const caseId = data.summary?.id;
      if (!caseId) return null;
      try {
        const report = await api.createReport(caseId, generatedBy);
        setData((current) => ({ ...current, report, verification: null }));
        return report;
      } catch (caught) {
        setError(
          caught instanceof ApiError
            ? caught.message
            : 'The report could not be generated.',
        );
        return null;
      }
    },
    [data.summary?.id],
  );

  const verify = useCallback(async () => {
    const caseId = data.summary?.id;
    const evidenceId = data.report?.evidence_id;
    if (!caseId || !evidenceId) return null;
    try {
      const verification = await api.verifyEvidence(caseId, evidenceId);
      setData((current) => ({ ...current, verification }));
      return verification;
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : 'The evidence hash could not be verified.',
      );
      return null;
    }
  }, [data.summary?.id, data.report?.evidence_id]);

  return {
    data,
    phase,
    events,
    liveCounts,
    percent,
    error,
    elapsedMs,
    start,
    cancel,
    reset,
    review,
    generateReport,
    verify,
    dismissError: () => setError(null),
  };
}
