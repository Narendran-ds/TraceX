/**
 * Probable exit points.
 *
 * This is the answer to the question the problem statement actually asks: which
 * exchange did the money leave through. Three things have to be true on this
 * screen or the answer is worthless in a case file:
 *
 *   * every named entity carries a source link a reviewer can open;
 *   * a cluster with no match is shown as unattributed, never upgraded to a
 *     probable name;
 *   * a bridge or mixer is shown as a boundary, with the trail stopping there.
 */

import { Badge, EmptyState, Notice, cx } from '../components/primitives';
import type { AttributionResponse, ExitCandidate } from '../contracts/api';
import { amount, percent, sourceLabel } from '../lib/format';

export function Attribution({
  attribution,
}: {
  attribution: AttributionResponse | null;
}) {
  if (!attribution) return null;

  const { candidates, coverage } = attribution;

  return (
    <div className="space-y-4 p-3.5">
      {attribution.trail_degraded && (
        <Notice tone="warn" title="Trail confidence degrades">
          {attribution.trail_degraded_note}
        </Notice>
      )}

      {candidates.length === 0 ? (
        <EmptyState
          title="No exit point identified"
          body="The traced activity did not reach a cluster that looks like a cash-out point, and no cluster matched the loaded attribution sources."
        />
      ) : (
        <>
          <h3 className="text-2xs font-medium uppercase tracking-wider text-type-faint">
            Ranked exit points
          </h3>
          <ol className="space-y-2">
            {candidates.map((candidate, index) => (
              <li key={candidate.cluster_id}>
                <ExitCard
                  candidate={candidate}
                  rank={index + 1}
                  unit={attribution.amount_unit}
                />
              </li>
            ))}
          </ol>
        </>
      )}

      {attribution.unattributed_terminal_clusters > 0 && (
        <p className="text-2xs leading-relaxed text-type-faint">
          {attribution.unattributed_terminal_clusters} terminal cluster
          {attribution.unattributed_terminal_clusters === 1 ? '' : 's'} had no match
          in the loaded sources and {attribution.unattributed_terminal_clusters === 1 ? 'is' : 'are'}{' '}
          reported as unattributed rather than assigned a probable name.
        </p>
      )}

      <section className="border-t border-ink-400 pt-3">
        <h3 className="text-2xs font-medium uppercase tracking-wider text-type-faint">
          Coverage
        </h3>
        <p className="mt-1.5 text-2xs leading-relaxed text-type-lo">
          {coverage.statement}
        </p>
        <ul className="mt-2 flex flex-wrap gap-1.5">
          {coverage.sources.map((source) => (
            <li key={source}>
              <Badge tone="outline">{sourceLabel(source)}</Badge>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function ExitCard({
  candidate,
  rank,
  unit,
}: {
  candidate: ExitCandidate;
  rank: number;
  unit: string;
}) {
  const isTop = rank === 1 && candidate.attributed && !candidate.confidence_boundary;

  return (
    <article
      className={cx(
        'rounded border p-3',
        candidate.confidence_boundary
          ? 'border-warn/35 bg-warn/[0.05]'
          : isTop
            ? 'border-trail/40 bg-trail-wash'
            : 'border-ink-400 bg-ink-800',
      )}
    >
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-mono text-2xs text-type-faint">{rank}</span>
            <h4
              className={cx(
                'truncate text-sm font-medium',
                candidate.attributed ? 'text-type-hi' : 'text-type-lo',
              )}
            >
              {candidate.entity_name ?? 'Unattributed exit cluster'}
            </h4>
          </div>
          <p className="mt-1 text-2xs text-type-faint">
            {candidate.cluster_label} · {candidate.member_count} address
            {candidate.member_count === 1 ? '' : 'es'} · hop {candidate.hop_depth}
            {candidate.entity_type && ` · ${candidate.entity_type}`}
          </p>
        </div>
        <div className="shrink-0 text-right">
          <div className="font-mono text-md leading-none tabular-nums text-type-hi">
            {percent(candidate.confidence)}
          </div>
          <div className="mt-1 text-2xs text-type-faint">confidence</div>
        </div>
      </header>

      <ConfidenceBar value={candidate.confidence} boundary={candidate.confidence_boundary} />

      <dl className="mt-2.5 flex items-baseline justify-between text-2xs">
        <dt className="text-type-faint">Value received</dt>
        <dd className="font-mono tabular-nums text-type-mid">
          {amount(candidate.amount_received, unit)}
        </dd>
      </dl>

      {candidate.confidence_boundary && candidate.boundary_note && (
        <p className="mt-2.5 border-t border-warn/25 pt-2 text-2xs leading-relaxed text-warn">
          {candidate.boundary_note}
        </p>
      )}

      {candidate.sources.length > 0 && (
        <div className="mt-2.5 border-t border-ink-400/70 pt-2">
          <p className="field-label mb-1.5">Sources</p>
          <ul className="space-y-1">
            {candidate.sources.map((source) => (
              <li
                key={`${source.source}-${source.matched_address}`}
                className="flex items-baseline justify-between gap-2 text-2xs"
              >
                <a
                  href={source.source_url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="truncate text-trail underline decoration-trail/40 underline-offset-2 hover:decoration-trail"
                  title={source.source_url}
                >
                  {sourceLabel(source.source)}
                </a>
                <span className="shrink-0 font-mono tabular-nums text-type-faint">
                  {percent(source.confidence)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <details className="group mt-2.5">
        <summary className="cursor-pointer list-none text-2xs text-type-faint transition-colors hover:text-type-lo">
          <span className="underline decoration-dotted underline-offset-2">
            How this confidence was reached
          </span>
        </summary>
        <ul className="mt-1.5 space-y-1">
          {candidate.confidence_basis.map((line, index) => (
            <li key={index} className="text-2xs leading-relaxed text-type-lo">
              {line}
            </li>
          ))}
        </ul>
      </details>
    </article>
  );
}

function ConfidenceBar({ value, boundary }: { value: number; boundary: boolean }) {
  return (
    <div
      className="mt-2.5 h-1 overflow-hidden rounded-full bg-ink-600"
      role="img"
      aria-label={`Confidence ${percent(value)}`}
    >
      <div
        className={cx(
          'h-full rounded-full transition-[width] duration-500 ease-out',
          boundary ? 'bg-warn' : 'bg-trail',
        )}
        style={{ width: `${Math.max(2, Math.round(value * 100))}%` }}
      />
    </div>
  );
}
