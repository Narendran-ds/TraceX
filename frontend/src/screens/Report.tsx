/**
 * Report preview and export.
 *
 * The one surface in the tool that turns to paper, because what it previews is
 * a document that goes into a case file. Everything on it is read back from the
 * API — this is a preview of the report, not a second implementation of it.
 */

import { useState } from 'react';
import { Badge, Button, Field, INPUT_CLASS, Notice, cx } from '../components/primitives';
import type {
  AttributionResponse,
  CaseSummary,
  FindingsResponse,
  ReportResponse,
  VerifyResponse,
} from '../contracts/api';
import { amount, dateOnly, percent, score as fmtScore, timestamp } from '../lib/format';

export function Report({
  summary,
  findings,
  attribution,
  report,
  verification,
  onGenerate,
  onVerify,
}: {
  summary: CaseSummary;
  findings: FindingsResponse | null;
  attribution: AttributionResponse | null;
  report: ReportResponse | null;
  verification: VerifyResponse | null;
  onGenerate: (generatedBy: string) => Promise<ReportResponse | null>;
  onVerify: () => Promise<VerifyResponse | null>;
}) {
  const [officer, setOfficer] = useState('');
  const [generating, setGenerating] = useState(false);
  const [verifying, setVerifying] = useState(false);

  const pending = findings?.findings.filter((f) => f.status === 'flagged').length ?? 0;
  const topExit = attribution?.candidates[0];

  const generate = async () => {
    setGenerating(true);
    await onGenerate(officer.trim() || 'Investigating officer');
    setGenerating(false);
  };

  const verify = async () => {
    setVerifying(true);
    await onVerify();
    setVerifying(false);
  };

  return (
    <div className="space-y-4 p-3.5">
      {pending > 0 && (
        <Notice tone="warn" title={`${pending} finding${pending === 1 ? '' : 's'} not yet reviewed`}>
          The report records each finding&rsquo;s review state. Anything you have not
          confirmed or rejected will be filed as awaiting review.
        </Notice>
      )}

      {/* --- the paper surface --- */}
      <div className="overflow-hidden rounded border border-paper-edge bg-paper text-paper-ink shadow-lift">
        <div className="space-y-3.5 p-5">
          <header className="border-b border-paper-edge pb-3">
            <h3 className="text-md font-semibold tracking-tight">
              Fund-Trail Investigation Report
            </h3>
            <p className="mt-1 text-2xs leading-relaxed text-paper-mid">
              Decision support for a victim-initiated cybercrime complaint. Every
              finding requires investigator confirmation and describes patterns, not
              conclusions about any person.
            </p>
          </header>

          <PaperSection title="Case">
            <PaperRow label="Complaint reference" value={summary.complaint_id} mono />
            <PaperRow label="Reported address" value={summary.wallet_address} mono wrap />
            <PaperRow label="Chain" value={summary.chain} />
            <PaperRow label="Analysis completed" value={timestamp(summary.completed_at)} />
          </PaperSection>

          <PaperSection title="Data provenance">
            <p
              className={cx(
                'text-2xs leading-relaxed',
                summary.provenance.kind === 'synthetic_scenario'
                  ? 'text-[oklch(0.48_0.13_50)]'
                  : 'text-paper-mid',
              )}
            >
              <strong className="font-semibold">{summary.provenance.label}.</strong>{' '}
              {summary.provenance.note}
            </p>
          </PaperSection>

          <PaperSection title="Financial summary">
            <PaperRow
              label="Amount traced from reported address"
              value={amount(summary.total_amount_traced, summary.amount_unit)}
              mono
            />
            <PaperRow
              label="Wallets discovered"
              value={String(summary.counts.wallets_discovered)}
              mono
            />
            <PaperRow
              label="Transactions analysed"
              value={String(summary.counts.transactions_analysed)}
              mono
            />
            <PaperRow
              label="Entity clusters identified"
              value={String(summary.counts.clusters_identified)}
              mono
            />
          </PaperSection>

          <PaperSection title="Assessment">
            <PaperRow
              label="Heuristic suspicion score"
              value={
                summary.score_was_capped
                  ? `${fmtScore(summary.raw_score_total)} raw, capped at ${fmtScore(summary.score_cap)} → ${fmtScore(summary.suspicion_score)}`
                  : `${fmtScore(summary.suspicion_score)} of ${fmtScore(summary.score_cap)}`
              }
              mono
            />
            <PaperRow label="Risk band" value={summary.risk_level ?? '—'} />
            <PaperRow
              label="Obfuscation patterns observed"
              value={summary.obfuscation_detected ? 'Yes' : 'No'}
            />
            <PaperRow
              label="Findings reviewed"
              value={`${summary.counts.findings_confirmed} confirmed, ${summary.counts.findings_rejected} rejected, ${summary.counts.findings_flagged} awaiting review`}
            />
          </PaperSection>

          <PaperSection title="Most likely exit point">
            {topExit ? (
              <>
                <PaperRow
                  label={topExit.entity_name ?? 'Unattributed exit cluster'}
                  value={`${percent(topExit.confidence)} confidence`}
                  mono
                />
                {topExit.sources[0] && (
                  <p className="mt-1 break-all text-2xs text-paper-mid">
                    Source: {topExit.sources[0].source_url}
                  </p>
                )}
                {topExit.confidence_boundary && (
                  <p className="mt-1.5 text-2xs leading-relaxed text-[oklch(0.48_0.13_50)]">
                    {topExit.boundary_note}
                  </p>
                )}
              </>
            ) : (
              <p className="text-2xs text-paper-mid">
                No exit point could be identified from the traced activity.
              </p>
            )}
          </PaperSection>

          <PaperSection title="Limitations">
            <p className="text-2xs leading-relaxed text-paper-mid">
              Attribution data is incomplete by nature, so an unmatched cluster
              means &ldquo;not present in the sources loaded here&rdquo;, not
              &ldquo;not an exchange&rdquo;. Funds passing through a mixing service
              or a cross-chain bridge cannot be followed deterministically on-chain.
              Clustering heuristics are inferences about common control, not proof
              of ownership. The suspicion score is a hand-tuned rule total, not a
              probability of criminality. No finding here asserts that any person
              committed an offence.
            </p>
          </PaperSection>

          <footer className="border-t border-paper-edge pt-2.5">
            <p className="text-2xs text-paper-mid">
              The generated PDF carries a SHA-256 hash over this evidence, so the
              document can be checked later against the record it was made from.
            </p>
          </footer>
        </div>
      </div>

      {/* --- export --- */}
      <section className="space-y-3 rounded-md border border-ink-400 bg-ink-800 p-3.5">
        <Field
          label="Generated by"
          htmlFor="officer"
          hint="Recorded on the report and in the case audit trail."
        >
          <input
            id="officer"
            className={INPUT_CLASS}
            placeholder="Name or badge number"
            value={officer}
            onChange={(event) => setOfficer(event.target.value)}
            autoComplete="off"
          />
        </Field>

        <div className="flex flex-wrap gap-2">
          <Button variant="primary" onClick={generate} loading={generating}>
            {report ? 'Regenerate PDF' : 'Generate PDF'}
          </Button>
          {report && (
            <>
              <Button
                onClick={() => window.open(report.download_url, '_blank', 'noopener')}
              >
                Open PDF
              </Button>
              <Button variant="ghost" onClick={verify} loading={verifying}>
                Verify hash
              </Button>
            </>
          )}
        </div>

        {report && (
          <div className="space-y-2 border-t border-ink-400 pt-3">
            <div>
              <p className="field-label">Evidence hash (SHA-256)</p>
              <p className="hash mt-1 break-all leading-relaxed">
                {report.evidence_hash}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-2xs text-type-faint">
              <span>Generated {timestamp(report.generated_at)}</span>
              <span aria-hidden="true">·</span>
              <span>by {report.generated_by}</span>
            </div>

            {verification && (
              <Notice tone={verification.match ? 'ok' : 'bad'}>
                <div className="flex items-center gap-2">
                  <Badge tone={verification.match ? 'ok' : 'bad'}>
                    {verification.match ? 'Hash matches' : 'Hash does not match'}
                  </Badge>
                  <span className="text-type-faint">
                    checked {timestamp(verification.verified_at)}
                  </span>
                </div>
                <p className="mt-1.5">
                  {verification.match
                    ? 'The stored evidence record hashes to the same value recorded on the report. It has not been altered since it was generated.'
                    : 'The stored evidence record no longer hashes to the value recorded on the report. Treat this record as modified.'}
                </p>
              </Notice>
            )}
          </div>
        )}
      </section>

      <p className="text-2xs text-type-faint">
        Report prepared {dateOnly(report?.generated_at ?? summary.completed_at)}.
      </p>
    </div>
  );
}

function PaperSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h4 className="text-2xs font-semibold uppercase tracking-wider text-paper-mid">
        {title}
      </h4>
      <div className="mt-1.5 space-y-0.5">{children}</div>
    </section>
  );
}

function PaperRow({
  label,
  value,
  mono,
  wrap,
}: {
  label: string;
  value: string;
  mono?: boolean;
  wrap?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-paper-edge/60 py-1 last:border-0">
      <span className="shrink-0 text-2xs text-paper-mid">{label}</span>
      <span
        className={cx(
          'text-right text-2xs text-paper-ink',
          mono && 'font-mono tabular-nums',
          wrap ? 'break-all' : 'truncate',
        )}
      >
        {value}
      </span>
    </div>
  );
}
