/**
 * Findings, the Why breakdown, and the review step.
 *
 * Three rules shape this screen:
 *
 *   * The arithmetic has to survive someone adding it up by hand. Contributions
 *     are listed, they sum to the raw total, and the cap is a visible line — not
 *     a silent clamp that makes the numbers not add up.
 *   * Every finding opens to the transactions behind it. A score an
 *     investigator cannot trace to hashes is not usable in a case file.
 *   * Nothing is concluded here. Each finding waits on Confirm or Reject, and
 *     rejecting one visibly moves the score.
 */

import { useState } from 'react';
import {
  Badge,
  Button,
  EmptyState,
  HashChip,
  Notice,
  cx,
} from '../components/primitives';
import type { FindingView, FindingsResponse } from '../contracts/api';
import { PATTERN_LABELS, score as fmtScore, shortHash, timestamp } from '../lib/format';

export function Findings({
  findings,
  onReview,
  onFocus,
  focusedId,
}: {
  findings: FindingsResponse | null;
  onReview: (id: string, action: 'confirm' | 'reject', note?: string) => void;
  onFocus: (finding: FindingView | null) => void;
  focusedId: string | null;
}) {
  if (!findings) return null;

  const { score, suppressed_signals: suppressed } = findings;
  const pending = findings.findings.filter((f) => f.status === 'flagged').length;

  return (
    <div className="space-y-4 p-3.5">
      <ScoreBreakdown score={score} />

      {findings.findings.length === 0 ? (
        <EmptyState
          title="No patterns from the library were observed"
          body={
            <>
              The detection engine ran and found none of its seven signals on this
              trail. That is not a finding of innocence — it means this particular
              set of shapes did not appear in the traced activity.
            </>
          }
        />
      ) : (
        <>
          <div className="flex items-baseline justify-between">
            <h3 className="text-2xs font-medium uppercase tracking-wider text-type-faint">
              Findings
            </h3>
            {pending > 0 && (
              <span className="text-2xs text-type-faint">
                {pending} awaiting your review
              </span>
            )}
          </div>

          <ul className="space-y-2">
            {findings.findings.map((finding) => (
              <li key={finding.id}>
                <FindingCard
                  finding={finding}
                  expanded={focusedId === finding.id}
                  onToggle={() =>
                    onFocus(focusedId === finding.id ? null : finding)
                  }
                  onReview={onReview}
                />
              </li>
            ))}
          </ul>
        </>
      )}

      {suppressed.length > 0 && <SuppressedSignals signals={suppressed} />}
    </div>
  );
}

/* ------------------------------------------------------------------ score */

function ScoreBreakdown({ score }: { score: FindingsResponse['score'] }) {
  const counted = score.contributions.filter((c) => c.status !== 'rejected');
  const rejected = score.contributions.filter((c) => c.status === 'rejected');

  return (
    <section className="rounded-md border border-ink-400 bg-ink-700/40 p-3">
      <h3 className="text-2xs font-medium uppercase tracking-wider text-type-faint">
        Why this score
      </h3>

      <table className="mt-2.5 w-full text-xs">
        <tbody>
          {counted.map((item) => (
            <tr key={item.id} className="align-baseline">
              <td className="py-1 pr-2 text-type-mid">
                {PATTERN_LABELS[item.pattern_type] ?? item.title}
                {item.status === 'confirmed' && (
                  <span className="ml-1.5 text-2xs text-ok">confirmed</span>
                )}
              </td>
              <td className="w-16 py-1 text-right text-2xs text-type-faint">
                {item.evidence_tx_hashes.length} tx
              </td>
              <td className="w-12 py-1 text-right font-mono tabular-nums text-type-hi">
                +{fmtScore(item.score_contribution)}
              </td>
            </tr>
          ))}

          {rejected.map((item) => (
            <tr key={item.id} className="align-baseline text-type-faint line-through decoration-type-faint/60">
              <td className="py-1 pr-2">
                {PATTERN_LABELS[item.pattern_type] ?? item.title}
              </td>
              <td className="py-1 text-right text-2xs">rejected</td>
              <td className="py-1 text-right font-mono tabular-nums">
                +{fmtScore(item.score_contribution)}
              </td>
            </tr>
          ))}
        </tbody>

        <tfoot>
          <tr className="border-t border-ink-500">
            <td className="pt-2 text-xs text-type-mid" colSpan={2}>
              Total
            </td>
            <td className="pt-2 text-right font-mono tabular-nums text-type-hi">
              {fmtScore(score.raw_total)}
            </td>
          </tr>
          {score.was_capped && (
            <tr>
              <td className="pt-1 text-2xs text-type-faint" colSpan={2}>
                Capped for presentation at {fmtScore(score.cap)}
              </td>
              <td className="pt-1 text-right font-mono tabular-nums text-trail">
                {fmtScore(score.capped_total)}
              </td>
            </tr>
          )}
        </tfoot>
      </table>

      {score.excluded_rejected_total > 0 && (
        <p className="mt-2 text-2xs leading-relaxed text-type-faint">
          {fmtScore(score.excluded_rejected_total)} points excluded because you
          rejected those findings.
        </p>
      )}

      <p className="mt-2.5 border-t border-ink-500/60 pt-2 text-2xs leading-relaxed text-type-faint">
        {score.weights_note}
      </p>
    </section>
  );
}

/* ---------------------------------------------------------------- finding */

function FindingCard({
  finding,
  expanded,
  onToggle,
  onReview,
}: {
  finding: FindingView;
  expanded: boolean;
  onToggle: () => void;
  onReview: (id: string, action: 'confirm' | 'reject', note?: string) => void;
}) {
  const [note, setNote] = useState('');
  const [noteOpen, setNoteOpen] = useState(false);

  const reviewed = finding.status !== 'flagged';

  return (
    <article
      className={cx(
        'rounded border transition-colors duration-150 ease-out',
        finding.status === 'rejected'
          ? 'border-ink-400 bg-ink-800/50'
          : expanded
            ? 'border-trail/40 bg-ink-700/50'
            : 'border-ink-400 bg-ink-800 hover:border-ink-500',
      )}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="flex w-full items-start gap-2.5 px-3 py-2.5 text-left"
      >
        <span
          aria-hidden="true"
          className={cx(
            'mt-1 h-1.5 w-1.5 shrink-0 rounded-full',
            finding.status === 'confirmed'
              ? 'bg-ok'
              : finding.status === 'rejected'
                ? 'bg-type-faint'
                : 'bg-warn',
          )}
        />
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline justify-between gap-2">
            <span
              className={cx(
                'text-sm font-medium',
                finding.status === 'rejected' ? 'text-type-faint' : 'text-type-hi',
              )}
            >
              {finding.title}
            </span>
            <span className="shrink-0 font-mono text-xs tabular-nums text-type-lo">
              +{fmtScore(finding.score_contribution)}
            </span>
          </span>
          <span className="mt-1 block text-xs leading-relaxed text-type-lo">
            {finding.description}
          </span>
        </span>
      </button>

      {expanded && (
        <div className="border-t border-ink-400/70 px-3 py-3 animate-fade-rise">
          <EvidenceDetail finding={finding} />
        </div>
      )}

      <div className="flex flex-wrap items-center gap-1.5 border-t border-ink-400/70 px-3 py-2">
        {reviewed ? (
          <>
            <Badge tone={finding.status === 'confirmed' ? 'ok' : 'neutral'}>
              {finding.status === 'confirmed' ? 'Confirmed' : 'Rejected'}
            </Badge>
            <span className="text-2xs text-type-faint">
              {finding.reviewed_by} · {timestamp(finding.reviewed_at)}
            </span>
            {finding.analyst_note && (
              <p className="mt-1 w-full text-2xs italic leading-relaxed text-type-lo">
                “{finding.analyst_note}”
              </p>
            )}
          </>
        ) : (
          <>
            <Button
              size="sm"
              variant="confirm"
              onClick={() => onReview(finding.id, 'confirm', note)}
            >
              Confirm
            </Button>
            <Button
              size="sm"
              variant="reject"
              onClick={() => onReview(finding.id, 'reject', note)}
            >
              Reject
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setNoteOpen((value) => !value)}
              aria-expanded={noteOpen}
            >
              {noteOpen ? 'Hide note' : 'Add note'}
            </Button>
            {noteOpen && (
              <textarea
                value={note}
                onChange={(event) => setNote(event.target.value)}
                rows={2}
                placeholder="What you observed, for the case file."
                className="mt-1.5 w-full rounded border border-ink-500 bg-ink-700 px-2.5 py-1.5 text-xs text-type-hi placeholder:text-type-faint focus:border-trail/60"
              />
            )}
          </>
        )}
      </div>
    </article>
  );
}

/* --------------------------------------------------------------- evidence */

const DETAIL_LABELS: Record<string, string> = {
  recipient_count: 'Recipients',
  threshold_recipients: 'Threshold',
  sender_count: 'Senders',
  threshold_senders: 'Threshold',
  window_seconds: 'Window',
  threshold_window_seconds: 'Window threshold',
  total_amount: 'Amount',
  chain_length: 'Chain length',
  threshold_hops: 'Minimum hops',
  total_peeled: 'Peeled total',
  final_remainder: 'Final remainder',
  part_count: 'Parts',
  threshold_parts: 'Minimum parts',
  transaction_count: 'Transactions',
  threshold_transactions: 'Threshold',
  active_days: 'Active for (days)',
  dormant_days: 'Dormant for (days)',
  burst_transaction_count: 'Burst transactions',
  member_count: 'Cluster size',
  amount_received: 'Received',
  hop_depth: 'Hop depth',
  destination_tx_count: 'Destination tx count',
};

function EvidenceDetail({ finding }: { finding: FindingView }) {
  const detail = finding.evidence_detail ?? {};

  const scalars = Object.entries(detail).filter(
    ([key, value]) =>
      DETAIL_LABELS[key] && (typeof value === 'number' || typeof value === 'string'),
  );

  const verdict = detail.disambiguation_verdict as string | undefined;

  return (
    <div className="space-y-3">
      {scalars.length > 0 && (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-2xs">
          {scalars.map(([key, value]) => (
            <div key={key} className="flex items-baseline justify-between gap-2">
              <dt className="text-type-faint">{DETAIL_LABELS[key]}</dt>
              <dd className="font-mono tabular-nums text-type-mid">
                {key.includes('window') ? `${value}s` : String(value)}
              </dd>
            </div>
          ))}
        </dl>
      )}

      {verdict && (
        <p className="text-2xs leading-relaxed text-type-lo">{verdict}</p>
      )}

      {Array.isArray(detail.path) && detail.path.length > 0 && (
        <div>
          <p className="field-label mb-1">Path</p>
          <ol className="space-y-0.5">
            {(detail.path as string[]).map((address, index) => (
              <li key={`${address}-${index}`} className="flex items-center gap-1.5">
                <span className="w-4 shrink-0 text-right font-mono text-2xs text-type-faint">
                  {index + 1}
                </span>
                <HashChip value={address} display={shortHash(address, 10, 6)} />
              </li>
            ))}
          </ol>
        </div>
      )}

      {Array.isArray(detail.matches) && (
        <div>
          <p className="field-label mb-1">Attribution sources</p>
          <ul className="space-y-1.5">
            {(detail.matches as Array<Record<string, string>>).map((match) => (
              <li key={`${match.source}-${match.entity_name}`} className="text-2xs">
                <span className="text-type-mid">{match.entity_name}</span>
                <span className="text-type-faint"> · {match.entity_type} · </span>
                <a
                  href={match.source_url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="text-trail underline decoration-trail/40 underline-offset-2 hover:decoration-trail"
                >
                  {match.source}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <p className="field-label mb-1">
          Backing transactions ({finding.evidence_tx_hashes.length})
        </p>
        <ul className="max-h-40 space-y-0.5 overflow-y-auto">
          {finding.evidence_tx_hashes.map((hash) => (
            <li key={hash}>
              <HashChip value={hash} display={shortHash(hash, 14, 8)} />
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- suppressed */

function SuppressedSignals({
  signals,
}: {
  signals: FindingsResponse['suppressed_signals'];
}) {
  return (
    <section>
      <h3 className="text-2xs font-medium uppercase tracking-wider text-type-faint">
        Checked and not flagged
      </h3>
      <p className="mt-1.5 text-2xs leading-relaxed text-type-faint">
        These shapes matched a pattern but were not reported. Shown so you can see
        what the engine decided against, rather than having to assume it looked.
      </p>
      <ul className="mt-2 space-y-2">
        {signals.map((signal, index) => (
          <li key={`${signal.subject}-${index}`}>
            <Notice tone="neutral">
              <span className="text-type-mid">
                {PATTERN_LABELS[signal.pattern_type] ?? signal.pattern_type}
              </span>{' '}
              on <span className="hash">{shortHash(signal.subject, 8, 6)}</span>
              <p className="mt-1 text-type-lo">{signal.reason}</p>
            </Notice>
          </li>
        ))}
      </ul>
    </section>
  );
}
