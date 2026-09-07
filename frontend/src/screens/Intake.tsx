/**
 * Case intake.
 *
 * The entry point mirrors what actually arrives at a cybercrime cell: a
 * complaint reference and one wallet address. Nothing else is asked for,
 * because nothing else is known yet.
 *
 * The prepared cases below exist so a demo can be driven without a terminal.
 * They prefill the form; the case is still opened and traced by the same code
 * path as any address typed by hand.
 */

import { useEffect, useState } from 'react';
import { Badge, Button, Field, INPUT_CLASS, Notice, cx } from '../components/primitives';
import { DEMO_CASES, type DemoCase } from '../lib/demoCases';
import type { Chain, HealthResponse } from '../contracts/api';
import { api } from '../lib/api';
import { count } from '../lib/format';

interface IntakeProps {
  onStart: (input: {
    complaint_id: string;
    wallet_address: string;
    chain: Chain;
  }) => void;
  busy: boolean;
  error: string | null;
}

export function Intake({ onStart, busy, error }: IntakeProps) {
  const [complaintId, setComplaintId] = useState('');
  const [address, setAddress] = useState('');
  const [chain, setChain] = useState<Chain>('ethereum');
  const [selected, setSelected] = useState<string | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .health()
      .then((value) => !cancelled && setHealth(value))
      .catch((caught) => !cancelled && setHealthError(caught.message));
    return () => {
      cancelled = true;
    };
  }, []);

  const applyDemo = (demo: DemoCase) => {
    setComplaintId(demo.complaintId);
    setAddress(demo.seedAddress);
    setChain(demo.chain);
    setSelected(demo.slug);
    setTouched(false);
  };

  const addressError =
    touched && !address.trim() ? 'A wallet address is required to open a case.' : null;
  const complaintError =
    touched && !complaintId.trim()
      ? 'A complaint reference ties this trace to a case file.'
      : null;

  const submit = () => {
    setTouched(true);
    if (!address.trim() || !complaintId.trim()) return;
    onStart({
      complaint_id: complaintId.trim(),
      wallet_address: address.trim(),
      chain,
    });
  };

  return (
    <div className="flex min-h-full flex-col">
      <header className="border-b border-ink-400/70 px-6 py-4">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4">
          <div className="flex items-baseline gap-3">
            <h1 className="text-sm font-semibold tracking-tight text-type-hi">
              Fund-Trail Investigation Assistant
            </h1>
            <span className="hidden text-2xs text-type-faint sm:inline">
              SIH26183 · Ministry of Home Affairs
            </span>
          </div>
          <ServiceStatus health={health} error={healthError} />
        </div>
      </header>

      <div className="mx-auto grid w-full max-w-5xl flex-1 content-start gap-x-12 gap-y-10 px-6 py-10 lg:grid-cols-[minmax(0,30rem)_minmax(0,1fr)] lg:py-16">
        {/* --- the form --- */}
        <div className="min-w-0">
          <p className="text-sm leading-relaxed text-type-mid">
            A complaint arrives with one artefact: the address the victim sent funds
            to. This traces where that money went, groups the addresses into the
            actors behind them, and flags the movement patterns worth an
            investigator&rsquo;s attention.
          </p>
          <p className="mt-3 text-sm leading-relaxed text-type-lo">
            It does not reach a conclusion on its own. Every finding it raises is
            put to you to confirm or reject before it reaches a report.
          </p>

          <div className="mt-9 space-y-5">
            <Field
              label="Complaint reference"
              htmlFor="complaint"
              error={complaintError}
              hint="The case number this trace will be filed under."
            >
              <input
                id="complaint"
                className={cx(INPUT_CLASS, 'font-mono')}
                placeholder="CYB-2026-00124"
                value={complaintId}
                onChange={(event) => setComplaintId(event.target.value)}
                autoComplete="off"
                spellCheck={false}
              />
            </Field>

            <Field
              label="Reported wallet address"
              htmlFor="address"
              error={addressError}
              hint="The address as reported by the victim."
            >
              <input
                id="address"
                className={cx(INPUT_CLASS, 'font-mono')}
                placeholder="0x… or bc1q…"
                value={address}
                onChange={(event) => setAddress(event.target.value)}
                onKeyDown={(event) => event.key === 'Enter' && submit()}
                autoComplete="off"
                spellCheck={false}
              />
            </Field>

            <Field label="Chain" hint="Which ledger the reported address belongs to.">
              <div className="flex gap-2" role="radiogroup" aria-label="Chain">
                {(['ethereum', 'bitcoin'] as Chain[]).map((option) => (
                  <button
                    key={option}
                    type="button"
                    role="radio"
                    aria-checked={chain === option}
                    onClick={() => setChain(option)}
                    className={cx(
                      'h-9 flex-1 rounded border text-sm font-medium capitalize',
                      'transition-colors duration-150 ease-out',
                      chain === option
                        ? 'border-trail/60 bg-trail-wash text-trail'
                        : 'border-ink-500 bg-ink-700 text-type-lo hover:bg-ink-600 hover:text-type-mid',
                    )}
                  >
                    {option}
                  </button>
                ))}
              </div>
            </Field>

            {error && (
              <Notice tone="bad" title="Could not open the case">
                {error}
              </Notice>
            )}

            <Button
              variant="primary"
              size="lg"
              className="w-full"
              onClick={submit}
              loading={busy}
            >
              {busy ? 'Tracing…' : 'Trace this address'}
            </Button>
          </div>
        </div>

        {/* --- prepared cases --- */}
        <aside className="min-w-0 lg:border-l lg:border-ink-400/70 lg:pl-10">
          <h2 className="text-2xs font-medium uppercase tracking-wider text-type-faint">
            Prepared cases
          </h2>
          <p className="mt-2 text-xs leading-relaxed text-type-faint">
            Cached traces, each chosen to show something different — including one
            where the trail dead-ends and one the engine deliberately does not flag.
            Selecting a case fills the form; the trace itself runs the same way.
          </p>

          <ul className="mt-4 space-y-2">
            {DEMO_CASES.map((demo) => {
              const isSelected = selected === demo.slug;
              return (
                <li key={demo.slug}>
                  <button
                    type="button"
                    onClick={() => applyDemo(demo)}
                    aria-pressed={isSelected}
                    className={cx(
                      'w-full rounded border px-3 py-2.5 text-left',
                      'transition-colors duration-150 ease-out',
                      isSelected
                        ? 'border-trail/50 bg-trail-wash'
                        : 'border-ink-400 bg-ink-800 hover:border-ink-500 hover:bg-ink-700',
                    )}
                  >
                    <div className="flex items-baseline justify-between gap-2">
                      <span
                        className={cx(
                          'font-mono text-xs',
                          isSelected ? 'text-trail' : 'text-type-lo',
                        )}
                      >
                        {demo.complaintId}
                      </span>
                      <span className="text-2xs capitalize text-type-faint">
                        {demo.chain}
                      </span>
                    </div>
                    <p
                      className={cx(
                        'mt-1 text-xs leading-snug',
                        isSelected ? 'text-type-hi' : 'text-type-mid',
                      )}
                    >
                      {demo.title}
                    </p>
                  </button>
                </li>
              );
            })}
          </ul>

          {health && (
            <dl className="mt-6 space-y-1.5 border-t border-ink-400/70 pt-4 text-2xs">
              <StatusRow
                label="Cached address histories"
                value={count(health.cache_entries)}
              />
              <StatusRow
                label="Attribution tags loaded"
                value={count(health.attribution_tags)}
              />
              <StatusRow
                label="Data source"
                value={health.adapter_mode === 'fixture' ? 'Cache (offline)' : 'Live + cache'}
              />
            </dl>
          )}
        </aside>
      </div>
    </div>
  );
}

function StatusRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-type-faint">{label}</dt>
      <dd className="font-mono tabular-nums text-type-lo">{value}</dd>
    </div>
  );
}

function ServiceStatus({
  health,
  error,
}: {
  health: HealthResponse | null;
  error: string | null;
}) {
  if (error) {
    return (
      <Badge tone="bad" title={error}>
        <Dot className="bg-bad" />
        Service unreachable
      </Badge>
    );
  }
  if (!health) {
    return (
      <Badge tone="neutral">
        <Dot className="bg-type-faint" />
        Checking
      </Badge>
    );
  }
  return (
    <Badge
      tone={health.offline_capable ? 'ok' : 'warn'}
      title={
        health.offline_capable
          ? 'Every prepared case is cached; the trace runs with the network off.'
          : 'No cached data found. Run: py -3.11 scripts/seed.py --reset'
      }
    >
      <Dot className={health.offline_capable ? 'bg-ok' : 'bg-warn'} />
      {health.offline_capable ? 'Runs offline' : 'Cache empty'}
    </Badge>
  );
}

function Dot({ className }: { className: string }) {
  return <span aria-hidden="true" className={cx('h-1.5 w-1.5 rounded-full', className)} />;
}
