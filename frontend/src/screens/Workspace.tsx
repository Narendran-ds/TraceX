/**
 * The investigation workspace.
 *
 * One screen, three zones: case identity across the top, the graph as the
 * dominant surface, and an inspector on the right that changes with what the
 * investigator is doing. Panels rather than pages, because a trace is one task
 * — sending someone to a different route to read the evidence behind a score
 * breaks the thing this tool exists to make easy.
 */

import { useEffect, useMemo, useState } from 'react';
import { Badge, Button, Stat, cx } from '../components/primitives';
import { TrailGraph } from '../components/TrailGraph';
import { Attribution } from './Attribution';
import { Findings } from './Findings';
import { Report } from './Report';
import type {
  CaseCounts,
  FindingView,
  GraphNode,
  ProgressEvent,
} from '../contracts/api';
import type { CaseData, Phase } from '../lib/useCase';
import { amount, count, duration, riskTone, score as fmtScore, shortHash } from '../lib/format';

type Tab = 'findings' | 'attribution' | 'report';

export function Workspace({
  data,
  phase,
  events,
  liveCounts,
  percent,
  elapsedMs,
  error,
  onReview,
  onGenerateReport,
  onVerify,
  onNewCase,
}: {
  data: CaseData;
  phase: Phase;
  events: ProgressEvent[];
  liveCounts: CaseCounts | null;
  percent: number;
  elapsedMs: number | null;
  error: string | null;
  onReview: (id: string, action: 'confirm' | 'reject', note?: string) => void;
  onGenerateReport: (by: string) => Promise<any>;
  onVerify: () => Promise<any>;
  onNewCase: () => void;
}) {
  const [tab, setTab] = useState<Tab>('findings');
  const [collapsed, setCollapsed] = useState(true);
  const [focusedFinding, setFocusedFinding] = useState<FindingView | null>(null);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);

  const running = phase === 'creating' || phase === 'running';
  const summary = data.summary;
  const graph = collapsed ? data.graphCollapsed : data.graphExpanded;

  // While the trace streams, reveal hops as their events arrive so the graph
  // grows outward instead of appearing all at once.
  const revealedHop = useMemo(() => {
    if (!running) return null;
    const hops = events.filter((event) => event.event === 'hop_complete');
    return hops.length ? (hops[hops.length - 1].hop ?? 0) : 0;
  }, [running, events]);

  // A finding selected in the inspector lights up the addresses behind it.
  const selectedIds = useMemo(() => {
    const ids = new Set<string>();
    if (selectedNode) ids.add(selectedNode.id);
    if (!focusedFinding || !data.clusters) return ids;

    const subjects = new Set(focusedFinding.subject_addresses.map((a) => a.toLowerCase()));
    if (collapsed) {
      data.clusters.clusters.forEach((cluster) => {
        if (cluster.members.some((member) => subjects.has(member.toLowerCase()))) {
          ids.add(cluster.id);
        }
      });
    } else {
      data.graphExpanded?.nodes.forEach((node) => {
        if (subjects.has(node.id.toLowerCase())) ids.add(node.id);
      });
    }
    return ids;
  }, [focusedFinding, selectedNode, collapsed, data.clusters, data.graphExpanded]);

  // Once a report exists, move the investigator to it — that is the end of the
  // task, and hunting for the tab is friction at exactly the wrong moment.
  useEffect(() => {
    if (data.report) setTab('report');
  }, [data.report]);

  const counts = liveCounts ?? summary?.counts ?? null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <CaseHeader
        summary={summary}
        running={running}
        elapsedMs={elapsedMs}
        onNewCase={onNewCase}
      />

      {running && <ProgressRail percent={percent} events={events} />}

      {error && (
        <div className="shrink-0 border-b border-bad/30 bg-bad/[0.07] px-4 py-2">
          <p className="text-xs text-bad">{error}</p>
        </div>
      )}

      <StatStrip counts={counts} summary={summary} graph={graph} />

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-px bg-ink-400/60 lg:grid-cols-[minmax(0,1fr)_25rem]">
        <TrailGraph
          graph={graph}
          collapsed={collapsed}
          onToggleCollapsed={setCollapsed}
          revealedHop={revealedHop}
          selectedIds={selectedIds}
          onSelectNode={setSelectedNode}
          className="min-h-[22rem] lg:min-h-0"
        />

        <aside className="flex min-h-0 flex-col bg-ink-800">
          <nav
            className="flex shrink-0 border-b border-ink-400"
            role="tablist"
            aria-label="Case inspector"
          >
            <TabButton
              active={tab === 'findings'}
              onClick={() => setTab('findings')}
              label="Findings"
              badge={
                summary?.counts.findings_flagged
                  ? String(summary.counts.findings_flagged)
                  : undefined
              }
            />
            <TabButton
              active={tab === 'attribution'}
              onClick={() => setTab('attribution')}
              label="Exit points"
              badge={
                data.attribution?.candidates.length
                  ? String(data.attribution.candidates.length)
                  : undefined
              }
            />
            <TabButton
              active={tab === 'report'}
              onClick={() => setTab('report')}
              label="Report"
            />
          </nav>

          <div className="min-h-0 flex-1 overflow-y-auto">
            {running ? (
              <InspectorPending />
            ) : tab === 'findings' ? (
              <Findings
                findings={data.findings}
                onReview={onReview}
                onFocus={setFocusedFinding}
                focusedId={focusedFinding?.id ?? null}
              />
            ) : tab === 'attribution' ? (
              <Attribution attribution={data.attribution} />
            ) : summary ? (
              <Report
                summary={summary}
                findings={data.findings}
                attribution={data.attribution}
                report={data.report}
                verification={data.verification}
                onGenerate={onGenerateReport}
                onVerify={onVerify}
              />
            ) : null}
          </div>
        </aside>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------- header */

function CaseHeader({
  summary,
  running,
  elapsedMs,
  onNewCase,
}: {
  summary: CaseData['summary'];
  running: boolean;
  elapsedMs: number | null;
  onNewCase: () => void;
}) {
  return (
    <header className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b border-ink-400 bg-ink-800 px-4 py-2.5">
      <div className="flex min-w-0 items-baseline gap-2.5">
        <span className="font-mono text-sm font-medium text-type-hi">
          {summary?.complaint_id ?? '—'}
        </span>
        <span className="hash truncate" title={summary?.wallet_address}>
          {summary ? shortHash(summary.wallet_address, 10, 8) : ''}
        </span>
        {summary && (
          <span className="text-2xs capitalize text-type-faint">{summary.chain}</span>
        )}
      </div>

      <div className="flex flex-1 flex-wrap items-center justify-end gap-2">
        {summary && (
          <Badge
            tone={summary.provenance.kind === 'live_cached' ? 'ok' : 'warn'}
            title={summary.provenance.note}
          >
            {summary.provenance.label}
          </Badge>
        )}
        {summary?.expansion_limited && (
          <Badge tone="warn" title={summary.expansion_note}>
            Expansion limited
          </Badge>
        )}
        {elapsedMs !== null && !running && (
          <span
            className="text-2xs text-type-faint"
            title="Wall-clock time for this trace, measured in the browser"
          >
            traced in {duration(elapsedMs / 1000)}
          </span>
        )}
        <Button size="sm" variant="ghost" onClick={onNewCase}>
          New case
        </Button>
      </div>
    </header>
  );
}

/* --------------------------------------------------------------- progress */

function ProgressRail({
  percent,
  events,
}: {
  percent: number;
  events: ProgressEvent[];
}) {
  const latest = events[events.length - 1];
  return (
    <div className="shrink-0 border-b border-ink-400 bg-ink-800">
      <div
        className="h-0.5 bg-ink-600"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Investigation progress"
      >
        <div
          className="h-full bg-trail transition-[width] duration-300 ease-out"
          style={{ width: `${percent}%` }}
        />
      </div>
      <p className="px-4 py-2 text-xs text-type-lo">
        {latest?.message ?? 'Starting the trace…'}
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ stats */

function StatStrip({
  counts,
  summary,
  graph,
}: {
  counts: CaseCounts | null;
  summary: CaseData['summary'];
  graph: CaseData['graphCollapsed'];
}) {
  const unit = summary?.amount_unit ?? '';

  return (
    <div className="shrink-0 border-b border-ink-400 bg-ink-800/60 px-4 py-3">
      <div className="flex flex-wrap items-start gap-x-8 gap-y-3">
        <Stat
          label="Risk"
          value={summary?.risk_level ?? '—'}
          tone={riskTone(summary?.risk_level)}
          hint="Band derived from the heuristic score. Documented thresholds, not tuned per case."
        />
        <Stat
          label="Score"
          value={
            summary?.suspicion_score === null || summary?.suspicion_score === undefined
              ? '—'
              : `${fmtScore(summary.suspicion_score)}/${fmtScore(summary.score_cap)}`
          }
          hint="Sum of the documented weights for every finding not rejected."
        />
        <Stat label="Wallets" value={count(counts?.wallets_discovered)} />
        <Stat label="Transactions" value={count(counts?.transactions_analysed)} />
        <Stat label="Clusters" value={count(counts?.clusters_identified)} />
        <Stat
          label="Traced"
          value={summary ? amount(summary.total_amount_traced, unit) : '—'}
          hint="Value that left the reported address, excluding dust."
        />
        <Stat
          label="Obfuscation"
          value={
            summary?.obfuscation_detected === null ||
            summary?.obfuscation_detected === undefined
              ? '—'
              : summary.obfuscation_detected
                ? 'Observed'
                : 'None observed'
          }
          tone={summary?.obfuscation_detected ? 'text-warn' : undefined}
          hint="Whether any shape pattern from the library survived review."
        />
        {graph?.collapse_ratio != null && (
          <Stat
            label="Collapse"
            value={`${graph.address_count}→${graph.cluster_count}`}
            hint="Traced addresses resolved into distinct actors."
          />
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------- tabs */

function TabButton({
  active,
  onClick,
  label,
  badge,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  badge?: string;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={cx(
        'relative flex-1 px-3 py-2.5 text-xs font-medium transition-colors duration-150 ease-out',
        active ? 'text-type-hi' : 'text-type-faint hover:text-type-mid',
      )}
    >
      <span className="inline-flex items-center gap-1.5">
        {label}
        {badge && (
          <span className="rounded-sm bg-warn/15 px-1 py-px font-mono text-2xs text-warn">
            {badge}
          </span>
        )}
      </span>
      {active && (
        <span
          aria-hidden="true"
          className="absolute inset-x-0 bottom-0 h-px bg-trail"
        />
      )}
    </button>
  );
}

function InspectorPending() {
  return (
    <div className="space-y-3 p-3.5">
      <p className="text-xs leading-relaxed text-type-faint">
        Findings appear once the graph is built and the detection engine has run
        over it. Nothing is scored until then.
      </p>
      <div className="space-y-2">
        {[0, 1, 2].map((index) => (
          <div
            key={index}
            className="h-16 animate-pulse rounded border border-ink-400 bg-ink-700/40"
            style={{ animationDelay: `${index * 120}ms` }}
          />
        ))}
      </div>
    </div>
  );
}
