/**
 * The shared component vocabulary.
 *
 * One button shape, one field shape, one badge shape, used everywhere. Each
 * interactive component carries default, hover, focus, active and disabled —
 * shipping half of those is what makes an interface feel subtly wrong.
 */

import type { ButtonHTMLAttributes, ReactNode } from 'react';

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ');
}

export { cx };

/* -- Button ------------------------------------------------------------- */

type ButtonVariant = 'primary' | 'default' | 'ghost' | 'confirm' | 'reject';
type ButtonSize = 'sm' | 'md' | 'lg';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  icon?: ReactNode;
}

const BUTTON_BASE =
  'inline-flex items-center justify-center gap-2 rounded border font-medium ' +
  'transition-[background-color,border-color,color,box-shadow] duration-150 ease-out ' +
  'disabled:cursor-not-allowed disabled:opacity-45 select-none';

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary:
    'bg-trail text-ink-900 border-trail hover:bg-trail/90 hover:border-trail/90 ' +
    'active:bg-trail-dim active:border-trail-dim',
  default:
    'bg-ink-700 text-type-hi border-ink-500 hover:bg-ink-600 hover:border-ink-500 ' +
    'active:bg-ink-700',
  ghost:
    'bg-transparent text-type-mid border-transparent hover:bg-ink-700 ' +
    'hover:text-type-hi active:bg-ink-600',
  confirm:
    'bg-transparent text-ok border-ok/45 hover:bg-ok/12 hover:border-ok/70 ' +
    'active:bg-ok/20',
  reject:
    'bg-transparent text-bad border-bad/45 hover:bg-bad/12 hover:border-bad/70 ' +
    'active:bg-bad/20',
};

const BUTTON_SIZES: Record<ButtonSize, string> = {
  sm: 'h-7 px-2.5 text-xs',
  md: 'h-9 px-3.5 text-sm',
  lg: 'h-11 px-5 text-md',
};

export function Button({
  variant = 'default',
  size = 'md',
  loading = false,
  icon,
  children,
  className,
  disabled,
  ...rest
}: ButtonProps) {
  return (
    <button
      type="button"
      className={cx(BUTTON_BASE, BUTTON_VARIANTS[variant], BUTTON_SIZES[size], className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? <Spinner /> : icon}
      {children}
    </button>
  );
}

function Spinner() {
  return (
    <span
      aria-hidden="true"
      className="h-3 w-3 shrink-0 rounded-full border-[1.5px] border-current border-r-transparent motion-safe:animate-spin"
    />
  );
}

/* -- Badge -------------------------------------------------------------- */

type BadgeTone = 'neutral' | 'trail' | 'ok' | 'warn' | 'bad' | 'outline';

const BADGE_TONES: Record<BadgeTone, string> = {
  neutral: 'bg-ink-700 text-type-mid border-ink-500',
  trail: 'bg-trail-wash text-trail border-trail/35',
  ok: 'bg-ok/12 text-ok border-ok/35',
  warn: 'bg-warn/12 text-warn border-warn/35',
  bad: 'bg-bad/12 text-bad border-bad/35',
  outline: 'bg-transparent text-type-lo border-ink-500',
};

export function Badge({
  tone = 'neutral',
  children,
  className,
  title,
}: {
  tone?: BadgeTone;
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cx(
        'inline-flex items-center gap-1.5 rounded-sm border px-1.5 py-0.5',
        'text-2xs font-medium leading-none whitespace-nowrap',
        BADGE_TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

/* -- Panel -------------------------------------------------------------- */

export function Panel({
  title,
  subtitle,
  actions,
  children,
  className,
  bodyClassName,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section
      className={cx(
        'flex min-h-0 flex-col rounded-md border border-ink-400 bg-ink-800 shadow-panel',
        className,
      )}
    >
      {(title || actions) && (
        <header className="flex shrink-0 items-center justify-between gap-3 border-b border-ink-400 px-3.5 py-2.5">
          <div className="min-w-0">
            {title && (
              <h2 className="truncate text-sm font-semibold text-type-hi">{title}</h2>
            )}
            {subtitle && (
              <p className="mt-0.5 truncate text-xs text-type-faint">{subtitle}</p>
            )}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-1.5">{actions}</div>}
        </header>
      )}
      <div className={cx('min-h-0 flex-1', bodyClassName ?? 'overflow-y-auto p-3.5')}>
        {children}
      </div>
    </section>
  );
}

/* -- Field -------------------------------------------------------------- */

export function Field({
  label,
  hint,
  error,
  htmlFor,
  children,
}: {
  label: string;
  hint?: ReactNode;
  error?: string | null;
  htmlFor?: string;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="field-label">
        {label}
      </label>
      {children}
      {error ? (
        <p role="alert" className="text-xs text-bad">
          {error}
        </p>
      ) : (
        hint && <p className="text-xs text-type-faint">{hint}</p>
      )}
    </div>
  );
}

export const INPUT_CLASS =
  'w-full rounded border border-ink-500 bg-ink-700 px-3 py-2 text-sm text-type-hi ' +
  'placeholder:text-type-faint transition-colors duration-150 ease-out ' +
  'hover:border-ink-500 focus:border-trail/60 disabled:opacity-50 ' +
  'disabled:cursor-not-allowed';

/* -- Stat --------------------------------------------------------------- */

/**
 * A single counter. Values arrive already formatted; this renders what it is
 * given and shows an em dash when the pipeline has not produced a number yet.
 */
export function Stat({
  label,
  value,
  tone,
  hint,
}: {
  label: string;
  value: string;
  tone?: string;
  hint?: string;
}) {
  return (
    <div className="min-w-0" title={hint}>
      <div className="field-label">{label}</div>
      <div
        className={cx(
          'mt-1 font-mono text-lg leading-none tabular-nums',
          tone ?? 'text-type-hi',
        )}
      >
        {value}
      </div>
    </div>
  );
}

/* -- Empty / error states ----------------------------------------------- */

export function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex h-full min-h-[8rem] flex-col items-center justify-center px-6 py-10 text-center">
      <p className="text-sm font-medium text-type-mid">{title}</p>
      <p className="mt-1.5 max-w-[46ch] text-xs leading-relaxed text-type-faint">{body}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function Notice({
  tone = 'warn',
  title,
  children,
}: {
  tone?: 'warn' | 'bad' | 'ok' | 'neutral';
  title?: string;
  children: ReactNode;
}) {
  const tones = {
    warn: 'border-warn/35 bg-warn/[0.07] text-warn',
    bad: 'border-bad/35 bg-bad/[0.07] text-bad',
    ok: 'border-ok/35 bg-ok/[0.07] text-ok',
    neutral: 'border-ink-500 bg-ink-700 text-type-mid',
  } as const;

  return (
    <div className={cx('rounded border px-3 py-2.5', tones[tone])}>
      {title && <p className="text-xs font-semibold">{title}</p>}
      <div className={cx('text-xs leading-relaxed', title && 'mt-1', 'text-type-mid')}>
        {children}
      </div>
    </div>
  );
}

/* -- Skeleton ----------------------------------------------------------- */

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cx('relative overflow-hidden rounded bg-ink-700', className)}
    >
      <div className="absolute inset-0 motion-safe:animate-sweep bg-gradient-to-r from-transparent via-ink-600 to-transparent" />
    </div>
  );
}

/* -- Copy-to-clipboard hash --------------------------------------------- */

export function HashChip({
  value,
  display,
  className,
}: {
  value: string;
  display?: string;
  className?: string;
}) {
  return (
    <button
      type="button"
      title={`${value}\n(click to copy)`}
      onClick={() => {
        void navigator.clipboard?.writeText(value).catch(() => {
          /* Clipboard may be blocked; the full value is in the tooltip. */
        });
      }}
      className={cx(
        'hash rounded-sm px-1 py-0.5 text-left transition-colors duration-150',
        'hover:bg-ink-700 hover:text-type-mid active:bg-ink-600',
        className,
      )}
    >
      {display ?? value}
    </button>
  );
}
