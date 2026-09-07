/**
 * The NDJSON stream reader.
 *
 * The case that matters and is easy to get wrong: a JSON object split across
 * two network chunks. That is normal, not an edge case, and a naive
 * split-on-newline-per-chunk reader silently drops events when it happens.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, investigate } from '../lib/api';
import type { ProgressEvent } from '../contracts/api';

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)));
      controller.close();
    },
  });
}

function mockStream(chunks: string[]) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(streamOf(chunks), { status: 200 })),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const started = '{"event":"started","message":"go","hop":null,"percent":2,"counts":null,"detail":{}}';
const done = '{"event":"complete","message":"done","hop":null,"percent":100,"counts":null,"detail":{}}';

describe('investigate', () => {
  it('emits one event per line', async () => {
    mockStream([`${started}\n${done}\n`]);
    const events: ProgressEvent[] = [];
    await investigate('case-1', (event) => events.push(event));

    expect(events.map((e) => e.event)).toEqual(['started', 'complete']);
    expect(events[1].percent).toBe(100);
  });

  it('reassembles an event split across chunks', async () => {
    const middle = Math.floor(started.length / 2);
    mockStream([started.slice(0, middle), `${started.slice(middle)}\n`, `${done}\n`]);

    const events: ProgressEvent[] = [];
    await investigate('case-1', (event) => events.push(event));

    expect(events).toHaveLength(2);
    expect(events[0].message).toBe('go');
  });

  it('emits a final line that arrives without a trailing newline', async () => {
    mockStream([`${started}\n`, done]);
    const events: ProgressEvent[] = [];
    await investigate('case-1', (event) => events.push(event));
    expect(events).toHaveLength(2);
  });

  it('skips a malformed line rather than killing the stream', async () => {
    mockStream([`${started}\nnot json at all\n${done}\n`]);
    const events: ProgressEvent[] = [];
    await investigate('case-1', (event) => events.push(event));
    expect(events.map((e) => e.event)).toEqual(['started', 'complete']);
  });

  it('ignores blank lines', async () => {
    mockStream([`${started}\n\n\n${done}\n`]);
    const events: ProgressEvent[] = [];
    await investigate('case-1', (event) => events.push(event));
    expect(events).toHaveLength(2);
  });

  it('surfaces a backend error event rather than throwing', async () => {
    const failure =
      '{"event":"error","message":"stopped early","hop":null,"percent":100,"counts":null,"detail":{}}';
    mockStream([`${started}\n${failure}\n`]);
    const events: ProgressEvent[] = [];
    await investigate('case-1', (event) => events.push(event));
    expect(events[1].event).toBe('error');
    expect(events[1].message).toBe('stopped early');
  });

  it('reports an unreachable service in words an operator can act on', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch');
      }),
    );

    await expect(investigate('case-1', () => {})).rejects.toMatchObject({
      code: 'service_unreachable',
    });
    await expect(investigate('case-1', () => {})).rejects.toThrow(/uvicorn/);
  });

  it('raises a readable error when the run cannot start', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('nope', { status: 404 })),
    );
    await expect(investigate('missing', () => {})).rejects.toBeInstanceOf(ApiError);
  });
});
