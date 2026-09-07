/**
 * Formatting must never invent a value.
 *
 * The rule these tests protect: a missing number renders as an em dash, because
 * "not yet computed" is information and a plausible-looking zero is a fabricated
 * metric on screen.
 */

import { describe, expect, it } from 'vitest';
import {
  EMPTY,
  amount,
  count,
  percent,
  riskTone,
  score,
  shortHash,
  sourceLabel,
  timestamp,
} from '../lib/format';

describe('missing values', () => {
  it('renders an em dash rather than a zero', () => {
    expect(amount(null)).toBe(EMPTY);
    expect(amount(undefined)).toBe(EMPTY);
    expect(count(null)).toBe(EMPTY);
    expect(score(null)).toBe(EMPTY);
    expect(percent(null)).toBe(EMPTY);
    expect(timestamp(null)).toBe(EMPTY);
  });

  it('renders a real zero as zero', () => {
    expect(amount(0)).toBe('0');
    expect(count(0)).toBe('0');
    expect(score(0)).toBe('0');
    expect(percent(0)).toBe('0%');
  });
});

describe('amount', () => {
  it('keeps chain precision without trailing noise', () => {
    expect(amount(9.94)).toBe('9.94');
    expect(amount(1.5, 'ETH')).toBe('1.5 ETH');
    expect(amount(12)).toBe('12');
  });

  it('uses more decimals for sub-unit values', () => {
    expect(amount(0.001234)).toBe('0.001234');
  });
});

describe('score', () => {
  it('does not add a decimal that is not there', () => {
    expect(score(97)).toBe('97');
    expect(score(97.5)).toBe('97.5');
  });
});

describe('percent', () => {
  it('rounds a 0-1 confidence to whole percent', () => {
    expect(percent(0.765)).toBe('77%');
    expect(percent(1)).toBe('100%');
  });
});

describe('shortHash', () => {
  it('truncates long values in the middle', () => {
    const hash = '0x' + 'a'.repeat(64);
    const short = shortHash(hash);
    expect(short).toContain('…');
    expect(short.length).toBeLessThan(hash.length);
  });

  it('leaves short values alone', () => {
    expect(shortHash('0xabc')).toBe('0xabc');
  });

  it('handles an empty value', () => {
    expect(shortHash('')).toBe(EMPTY);
  });
});

describe('sourceLabel', () => {
  it('names the sources an investigator will cite', () => {
    expect(sourceLabel('ofac_sdn')).toBe('OFAC SDN');
    expect(sourceLabel('graphsense_tagpack')).toBe('GraphSense TagPack');
  });

  it('degrades readably for an unknown source', () => {
    expect(sourceLabel('some_new_source')).toBe('some new source');
  });
});

describe('riskTone', () => {
  it('maps each band to its own tone and nothing to a default colour', () => {
    expect(riskTone('HIGH')).toContain('risk-high');
    expect(riskTone('MEDIUM')).toContain('risk-medium');
    expect(riskTone('LOW')).toContain('risk-low');
    expect(riskTone(null)).toContain('faint');
  });
});
