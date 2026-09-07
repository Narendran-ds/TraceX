/**
 * The follow-the-money graph.
 *
 * Two things carry the whole demo here, and both are state, not decoration:
 *
 *   1. Hop reveal. Nodes appear in hop order as the trace runs, so the graph
 *      builds outward from the reported address rather than snapping into
 *      existence. Under reduced motion the whole graph is simply present.
 *   2. Cluster collapse. Toggling between address view and actor view is the
 *      moment the trace becomes legible — hundreds of addresses resolving into
 *      the handful of actors behind them.
 *
 * Everything off the primary trail is dimmed rather than hidden: an
 * investigator needs to see that the other branches exist and were considered.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import type { GraphEdge, GraphNode, GraphResponse, NodeRole } from '../contracts/api';
import { ROLE_COLORS, ROLE_LABELS, amount, shortHash } from '../lib/format';
import { cx } from './primitives';

const GROUND = 'oklch(0.165 0.011 250)';
const EDGE_IDLE = 'oklch(0.360 0.014 250)';
const EDGE_DIM = 'oklch(0.270 0.012 250)';
const TRAIL = 'oklch(0.790 0.150 72)';
const LABEL = 'oklch(0.795 0.010 250)';
const LABEL_DIM = 'oklch(0.500 0.014 250)';

interface RenderNode extends GraphNode {
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
  radius: number;
}

interface RenderLink {
  source: string | RenderNode;
  target: string | RenderNode;
  raw: GraphEdge;
}

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () =>
      typeof window !== 'undefined' &&
      window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true,
  );
  useEffect(() => {
    const query = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    if (!query) return;
    const handler = (event: MediaQueryListEvent) => setReduced(event.matches);
    query.addEventListener('change', handler);
    return () => query.removeEventListener('change', handler);
  }, []);
  return reduced;
}

/** Node radius scales with what the node represents, within tight bounds. */
function radiusFor(node: GraphNode): number {
  const base = node.kind === 'cluster' ? 5 + Math.min(9, Math.sqrt(node.member_count) * 2.6) : 5;
  return node.role === 'suspect' ? base + 2.5 : base;
}

export function TrailGraph({
  graph,
  collapsed,
  onToggleCollapsed,
  revealedHop,
  selectedIds,
  onSelectNode,
  className,
}: {
  graph: GraphResponse | null;
  collapsed: boolean;
  onToggleCollapsed: (next: boolean) => void;
  /** Highest hop revealed so far while the trace streams. null = show all. */
  revealedHop: number | null;
  /** Addresses or cluster ids to highlight, e.g. from a selected finding. */
  selectedIds: Set<string>;
  onSelectNode: (node: GraphNode | null) => void;
  className?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<any>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [hovered, setHovered] = useState<string | null>(null);
  const [dimOffTrail, setDimOffTrail] = useState(true);
  const reducedMotion = usePrefersReducedMotion();

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      setSize({
        width: Math.floor(entry.contentRect.width),
        height: Math.floor(entry.contentRect.height),
      });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const visible = useMemo(() => {
    if (!graph) return { nodes: [] as RenderNode[], links: [] as RenderLink[] };

    const limit = revealedHop === null ? Number.POSITIVE_INFINITY : revealedHop;
    const nodes: RenderNode[] = graph.nodes
      .filter((node) => node.hop_depth <= limit)
      .map((node) => ({ ...node, radius: radiusFor(node) }));

    const present = new Set(nodes.map((node) => node.id));
    const links: RenderLink[] = graph.edges
      .filter((edge) => present.has(edge.source) && present.has(edge.target))
      .map((edge) => ({ source: edge.source, target: edge.target, raw: edge }));

    return { nodes, links };
  }, [graph, revealedHop]);

  // Feed the force simulation only when the node set actually changes, so a
  // hover or a selection does not restart the layout.
  const dataKey = useMemo(
    () => `${collapsed}:${visible.nodes.map((n) => n.id).join('|')}`,
    [collapsed, visible.nodes],
  );
  const dataRef = useRef<{ key: string; data: any }>({ key: '', data: { nodes: [], links: [] } });
  if (dataRef.current.key !== dataKey) {
    dataRef.current = { key: dataKey, data: { nodes: visible.nodes, links: visible.links } };
  }

  const anyTrail = useMemo(
    () => visible.nodes.some((node) => node.on_primary_trail),
    [visible.nodes],
  );

  const paintNode = useCallback(
    (node: RenderNode, ctx: CanvasRenderingContext2D, scale: number) => {
      const x = node.x ?? 0;
      const y = node.y ?? 0;
      const isSelected = selectedIds.has(node.id);
      const isHovered = hovered === node.id;
      const onTrail = node.on_primary_trail;
      const muted = dimOffTrail && anyTrail && !onTrail && !isSelected && !isHovered;

      const color = ROLE_COLORS[node.role as NodeRole] ?? ROLE_COLORS.intermediary;

      ctx.save();
      ctx.globalAlpha = muted ? 0.3 : 1;

      // A halo marks the addresses an investigator is currently looking at.
      if (isSelected || isHovered) {
        ctx.beginPath();
        ctx.arc(x, y, node.radius + 5, 0, Math.PI * 2);
        ctx.fillStyle = TRAIL;
        ctx.globalAlpha = 0.16;
        ctx.fill();
        ctx.globalAlpha = 1;
      }

      ctx.beginPath();
      ctx.arc(x, y, node.radius, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.lineWidth = 1.5 / scale;
      ctx.strokeStyle = onTrail && !muted ? TRAIL : GROUND;
      ctx.stroke();

      // Cluster nodes carry their member count: this is where the collapse
      // becomes readable rather than just prettier.
      if (node.kind === 'cluster' && node.member_count > 1) {
        ctx.font = `600 ${Math.max(7, node.radius * 0.95)}px ui-sans-serif, system-ui, sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = GROUND;
        ctx.fillText(String(node.member_count), x, y + 0.5);
      }

      // Labels appear once there is room for them to be read.
      const showLabel =
        scale > 1.15 || isHovered || isSelected || onTrail || node.role === 'suspect';
      if (showLabel) {
        const label = node.attributed_entity ?? (node.kind === 'cluster'
          ? node.label
          : shortHash(node.id, 6, 4));
        const fontSize = 11 / scale;
        ctx.font = `${fontSize}px ui-sans-serif, system-ui, sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        const labelY = y + node.radius + 5 / scale;

        // A stroked halo in the ground colour keeps the label legible over an
        // edge or a node, without the rectangle a filled plate leaves behind.
        ctx.lineJoin = 'round';
        ctx.lineWidth = 3 / scale;
        ctx.strokeStyle = GROUND;
        ctx.strokeText(label, x, labelY);

        ctx.fillStyle = muted ? LABEL_DIM : onTrail ? TRAIL : LABEL;
        ctx.fillText(label, x, labelY);
      }

      ctx.restore();
    },
    [selectedIds, hovered, dimOffTrail, anyTrail],
  );

  const paintPointerArea = useCallback(
    (node: RenderNode, color: string, ctx: CanvasRenderingContext2D) => {
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(node.x ?? 0, node.y ?? 0, node.radius + 4, 0, Math.PI * 2);
      ctx.fill();
    },
    [],
  );

  const linkColor = useCallback(
    (link: RenderLink) => {
      if (link.raw.on_primary_trail) return TRAIL;
      return dimOffTrail && anyTrail ? EDGE_DIM : EDGE_IDLE;
    },
    [dimOffTrail, anyTrail],
  );

  const linkWidth = useCallback(
    (link: RenderLink) => (link.raw.on_primary_trail ? 2.2 : 1),
    [],
  );

  // Spread the layout enough that labels have somewhere to sit. Defaults pack
  // nodes so tightly that a cluster label lands on top of its own node.
  useEffect(() => {
    const instance = graphRef.current;
    if (!instance) return;
    instance.d3Force('charge')?.strength(-260).distanceMax(600);
    instance.d3Force('link')?.distance(72);
  }, [dataKey]);

  // Instant, not animated. Several overlapping animated zoomToFit calls race
  // each other and settle on a transform that fits neither the start nor the
  // end state. The reveal here is the hop-by-hop build, not the zoom, so a
  // snap costs nothing.
  const fit = useCallback(() => {
    graphRef.current?.zoomToFit(0, 72);
  }, []);

  // Fit once the simulation has settled. Fitting mid-layout frames the graph
  // around positions it is about to leave, which is what leaves it huddled in
  // one corner of an otherwise empty panel.
  // Fit once the layout has settled (onEngineStop), with a late backstop in
  // case the engine never reports stopping.
  useEffect(() => {
    if (!graphRef.current || visible.nodes.length === 0) return;
    const timer = window.setTimeout(fit, 2600);
    return () => window.clearTimeout(timer);
  }, [dataKey, fit, visible.nodes.length]);

  const hasGraph = Boolean(graph && graph.nodes.length > 0);

  return (
    <div className={cx('relative min-h-0 overflow-hidden bg-ink-900', className)}>
      <div ref={containerRef} className="absolute inset-0">
        {hasGraph && size.width > 0 && (
          <ForceGraph2D
            ref={graphRef}
            width={size.width}
            height={size.height}
            graphData={dataRef.current.data}
            backgroundColor="rgba(0,0,0,0)"
            nodeRelSize={1}
            nodeCanvasObject={paintNode as any}
            nodePointerAreaPaint={paintPointerArea as any}
            linkColor={linkColor as any}
            linkWidth={linkWidth as any}
            linkDirectionalArrowLength={4}
            linkDirectionalArrowRelPos={1}
            linkDirectionalArrowColor={linkColor as any}
            linkDirectionalParticles={reducedMotion ? 0 : ((link: any) =>
              link.raw.on_primary_trail ? 2 : 0) as any}
            linkDirectionalParticleWidth={2.5}
            linkDirectionalParticleColor={(() => TRAIL) as any}
            linkDirectionalParticleSpeed={0.006}
            warmupTicks={reducedMotion ? 120 : 40}
            cooldownTicks={reducedMotion ? 0 : 140}
            d3VelocityDecay={0.28}
            onEngineStop={fit}
            onNodeHover={(node: any) => setHovered(node ? node.id : null)}
            onNodeClick={(node: any) => onSelectNode(node ?? null)}
            onBackgroundClick={() => onSelectNode(null)}
            enableNodeDrag
          />
        )}
      </div>

      {!hasGraph && (
        <div className="absolute inset-0 grid place-items-center px-8 text-center">
          <p className="max-w-[42ch] text-xs leading-relaxed text-type-faint">
            The graph draws itself as the trace runs — the reported address first,
            then each hop outward from it.
          </p>
        </div>
      )}

      {/* --- controls, top-left --- */}
      {hasGraph && (
        <div className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between gap-3 p-3">
          <div className="pointer-events-auto flex items-center gap-1 rounded-md border border-ink-400 bg-ink-800/90 p-1 backdrop-blur-sm">
            <ViewToggle
              active={collapsed}
              onClick={() => onToggleCollapsed(true)}
              label="Actors"
              hint="Group addresses into the entities that appear to control them"
            />
            <ViewToggle
              active={!collapsed}
              onClick={() => onToggleCollapsed(false)}
              label="Addresses"
              hint="Show every traced address individually"
            />
          </div>

          <div className="pointer-events-auto flex items-center gap-1.5">
            {anyTrail && (
              <button
                type="button"
                onClick={() => setDimOffTrail((value) => !value)}
                aria-pressed={dimOffTrail}
                title="Dim everything that is not on the highest-value path"
                className={cx(
                  'h-7 rounded border px-2.5 text-2xs font-medium',
                  'transition-colors duration-150 ease-out',
                  dimOffTrail
                    ? 'border-trail/50 bg-trail-wash text-trail'
                    : 'border-ink-400 bg-ink-800/90 text-type-lo hover:text-type-mid',
                )}
              >
                Primary trail
              </button>
            )}
            <button
              type="button"
              onClick={fit}
              title="Fit the graph to the panel"
              className="h-7 rounded border border-ink-400 bg-ink-800/90 px-2.5 text-2xs font-medium text-type-lo transition-colors duration-150 ease-out hover:text-type-mid"
            >
              Fit
            </button>
          </div>
        </div>
      )}

      {/* --- legend + collapse readout, bottom --- */}
      {hasGraph && graph && (
        <div className="pointer-events-none absolute inset-x-0 bottom-0 flex flex-wrap items-end justify-between gap-3 p-3">
          <ul className="pointer-events-auto flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-md border border-ink-400 bg-ink-800/90 px-2.5 py-1.5 backdrop-blur-sm">
            {(
              ['suspect', 'intermediary', 'exchange', 'mixer', 'bridge'] as NodeRole[]
            ).map((role) => (
              <li key={role} className="flex items-center gap-1.5">
                <span
                  aria-hidden="true"
                  className="h-2 w-2 rounded-full"
                  style={{ background: ROLE_COLORS[role] }}
                />
                <span className="text-2xs text-type-lo">{ROLE_LABELS[role]}</span>
              </li>
            ))}
          </ul>

          {collapsed && graph.collapse_ratio !== null && (
            <p className="pointer-events-auto rounded-md border border-ink-400 bg-ink-800/90 px-2.5 py-1.5 text-2xs text-type-lo backdrop-blur-sm">
              <span className="font-mono tabular-nums text-type-hi">
                {graph.address_count}
              </span>{' '}
              addresses resolve to{' '}
              <span className="font-mono tabular-nums text-trail">
                {graph.cluster_count}
              </span>{' '}
              actors
            </p>
          )}
        </div>
      )}

      {/* --- hovered node readout --- */}
      {hovered && graph && <HoverCard graph={graph} nodeId={hovered} />}
    </div>
  );
}

function ViewToggle({
  active,
  onClick,
  label,
  hint,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  hint: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      title={hint}
      className={cx(
        'h-6 rounded-sm px-2.5 text-2xs font-medium transition-colors duration-150 ease-out',
        active
          ? 'bg-trail-wash text-trail'
          : 'text-type-faint hover:bg-ink-700 hover:text-type-mid',
      )}
    >
      {label}
    </button>
  );
}

function HoverCard({ graph, nodeId }: { graph: GraphResponse; nodeId: string }) {
  const node = graph.nodes.find((candidate) => candidate.id === nodeId);
  if (!node) return null;

  return (
    <div className="pointer-events-none absolute right-3 top-14 w-64 rounded-md border border-ink-400 bg-ink-800/95 p-2.5 shadow-lift backdrop-blur-sm animate-fade-rise">
      <div className="flex items-center gap-2">
        <span
          aria-hidden="true"
          className="h-2 w-2 shrink-0 rounded-full"
          style={{ background: ROLE_COLORS[node.role as NodeRole] }}
        />
        <span className="truncate text-xs font-medium text-type-hi">
          {node.attributed_entity ?? node.label}
        </span>
      </div>
      {node.kind === 'address' && (
        <p className="hash mt-1.5 break-all">{node.id}</p>
      )}
      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-2xs">
        <HoverRow label="Role" value={ROLE_LABELS[node.role as NodeRole]} />
        <HoverRow label="Hop" value={String(node.hop_depth)} />
        <HoverRow label="Received" value={amount(node.total_in, graph.amount_unit)} />
        <HoverRow label="Sent" value={amount(node.total_out, graph.amount_unit)} />
        {node.kind === 'cluster' && (
          <HoverRow label="Addresses" value={String(node.member_count)} />
        )}
        {node.attribution_source && (
          <HoverRow label="Source" value={node.attribution_source} />
        )}
      </dl>
    </div>
  );
}

function HoverRow({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-type-faint">{label}</dt>
      <dd className="truncate text-right font-mono tabular-nums text-type-mid">{value}</dd>
    </>
  );
}
