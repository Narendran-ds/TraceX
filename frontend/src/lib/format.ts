/**
 * Display formatting.
 *
 * Every function here takes a value the backend computed and renders it. None
 * of them invents, rounds up, or supplies a fallback number — a missing value
 * renders as an em dash, because "not yet computed" is information and a
 * plausible-looking zero is not.
 */

import type { NodeRole } from '../contracts/api';

export const EMPTY = '—';

/** Truncate an address or hash for display. The full value stays copyable. */
export function shortHash(value: string, head = 6, tail = 4): string {
  if (!value) return EMPTY;
  if (value.length <= head + tail + 2) return value;
  return `${value.slice(0, head)}…${value.slice(-tail)}`;
}

/** Chain amounts, at the precision the chain actually uses. */
export function amount(value: number | null | undefined, unit = ''): string {
  if (value === null || value === undefined || Number.isNaN(value)) return EMPTY;
  const digits = Math.abs(value) >= 1 ? 4 : 6;
  const text = value
    .toFixed(digits)
    .replace(/(\.\d*?)0+$/, '$1')
    .replace(/\.$/, '');
  return unit ? `${text} ${unit}` : text;
}

export function count(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return EMPTY;
  return value.toLocaleString('en-IN');
}

/** Scores carry one decimal only when they have one. */
export function score(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return EMPTY;
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

export function percent(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return EMPTY;
  return `${Math.round(value * 100)}%`;
}

export function timestamp(value: string | null | undefined): string {
  if (!value) return EMPTY;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

export function dateOnly(value: string | null | undefined): string {
  if (!value) return EMPTY;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  });
}

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return EMPTY;
  // A sub-second trace is the honest number, but rounding it to "0s" reads as
  // a broken clock rather than a fast one.
  if (seconds < 10) return `${seconds.toFixed(1)}s`;
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
}

/** Pattern identifiers as an investigator would read them. */
export const PATTERN_LABELS: Record<string, string> = {
  fan_out: 'Rapid fan-out',
  fan_in: 'Fan-in consolidation',
  peel_chain: 'Peel chain',
  round_split: 'Round-number splitting',
  timing_burst: 'Timing burst',
  sanctioned_match: 'Sanctioned / mixer match',
  cluster_context: 'Short-lived cluster',
};

export const ROLE_LABELS: Record<NodeRole, string> = {
  suspect: 'Reported address',
  victim: 'Victim',
  intermediary: 'Intermediary',
  exchange: 'Exchange',
  mixer: 'Mixer / sanctioned',
  bridge: 'Bridge',
};

export const ROLE_COLORS: Record<NodeRole, string> = {
  suspect: 'oklch(0.780 0.155 62)',
  victim: 'oklch(0.720 0.115 245)',
  intermediary: 'oklch(0.640 0.028 250)',
  exchange: 'oklch(0.760 0.130 172)',
  mixer: 'oklch(0.680 0.185 24)',
  bridge: 'oklch(0.720 0.140 305)',
};

export const SOURCE_LABELS: Record<string, string> = {
  ofac_sdn: 'OFAC SDN',
  graphsense_tagpack: 'GraphSense TagPack',
  manual_curated: 'Curated',
};

export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source.replace(/_/g, ' ');
}

export function riskTone(level: string | null | undefined): string {
  switch (level) {
    case 'HIGH':
      return 'text-risk-high';
    case 'MEDIUM':
      return 'text-risk-medium';
    case 'LOW':
      return 'text-risk-low';
    default:
      return 'text-type-faint';
  }
}
