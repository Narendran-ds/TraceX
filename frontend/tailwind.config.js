/**
 * Design tokens.
 *
 * The scene: a district cybercrime officer, late, on a laptop, with a victim
 * waiting for an answer and a complaint number already attached to the case.
 * That argues for an instrument, not a browser tab.
 *
 * Dark, because the centrepiece interaction is one fund trail lighting up while
 * everything else recedes — that only works on a dark ground. Deliberately NOT
 * terminal-green and NOT navy SaaS: the ground is a deep desaturated ink and the
 * trail burns ochre, like a lit path across a case file. The one surface that
 * turns to paper is the report, because a report is a document.
 *
 * Colour strategy: Restrained. Ochre carries selection, the primary trail and
 * primary actions. Role and risk colours are semantic and appear nowhere
 * decorative.
 *
 * No webfonts anywhere: the demo has to survive with the network off.
 */

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // --- ground: the instrument ---
        ink: {
          900: 'oklch(0.165 0.011 250 / <alpha-value>)', // app ground
          800: 'oklch(0.205 0.012 250 / <alpha-value>)', // panel
          700: 'oklch(0.245 0.012 250 / <alpha-value>)', // raised panel / input
          600: 'oklch(0.295 0.013 250 / <alpha-value>)', // hover
          500: 'oklch(0.355 0.014 250 / <alpha-value>)', // border strong
          400: 'oklch(0.305 0.013 250 / <alpha-value>)', // border
        },
        // --- text ramp, contrast-checked against ink-800/900 ---
        type: {
          hi: 'oklch(0.965 0.004 250 / <alpha-value>)',  // 15.5:1 on ink-800
          mid: 'oklch(0.795 0.010 250 / <alpha-value>)', //  8.4:1 on ink-800
          lo: 'oklch(0.680 0.013 250 / <alpha-value>)',  //  5.3:1 on ink-800 — still body-safe
          faint: 'oklch(0.560 0.014 250 / <alpha-value>)', // >=18px or non-essential only
        },
        // --- the trail ---
        trail: {
          DEFAULT: 'oklch(0.790 0.150 72 / <alpha-value>)',
          dim: 'oklch(0.640 0.120 72 / <alpha-value>)',
          deep: 'oklch(0.420 0.090 72 / <alpha-value>)',
          wash: 'oklch(0.255 0.040 72 / <alpha-value>)',
        },
        // --- semantic roles: graph nodes, cluster badges ---
        role: {
          suspect: 'oklch(0.780 0.155 62 / <alpha-value>)',
          victim: 'oklch(0.720 0.115 245 / <alpha-value>)',
          intermediary: 'oklch(0.640 0.028 250 / <alpha-value>)',
          exchange: 'oklch(0.760 0.130 172 / <alpha-value>)',
          mixer: 'oklch(0.680 0.185 24 / <alpha-value>)',
          bridge: 'oklch(0.720 0.140 305 / <alpha-value>)',
        },
        // --- risk bands ---
        risk: {
          high: 'oklch(0.680 0.185 24 / <alpha-value>)',
          medium: 'oklch(0.790 0.150 72 / <alpha-value>)',
          low: 'oklch(0.760 0.130 172 / <alpha-value>)',
          none: 'oklch(0.640 0.028 250 / <alpha-value>)',
        },
        // --- state ---
        ok: 'oklch(0.760 0.130 172 / <alpha-value>)',
        warn: 'oklch(0.790 0.150 72 / <alpha-value>)',
        bad: 'oklch(0.680 0.185 24 / <alpha-value>)',
        // --- the paper surface: report preview only ---
        paper: {
          DEFAULT: 'oklch(0.965 0.006 85 / <alpha-value>)',
          edge: 'oklch(0.895 0.010 85 / <alpha-value>)',
          ink: 'oklch(0.235 0.012 250 / <alpha-value>)',
          mid: 'oklch(0.450 0.014 250 / <alpha-value>)',
        },
      },
      fontFamily: {
        // System stacks only. A webfont that fails to load offline would take
        // the demo's typography with it.
        sans: [
          'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI Variable Text',
          'Segoe UI', 'Roboto', 'Helvetica Neue', 'Arial', 'sans-serif',
        ],
        mono: [
          'ui-monospace', 'Cascadia Mono', 'Cascadia Code', 'Segoe UI Mono',
          'Consolas', 'Menlo', 'Liberation Mono', 'monospace',
        ],
      },
      fontSize: {
        // Fixed rem scale at ratio ~1.15. Product UI is viewed at consistent
        // DPI; fluid clamp headings only make panels look wrong.
        '2xs': ['0.6875rem', { lineHeight: '1rem', letterSpacing: '0.02em' }],
        xs: ['0.75rem', { lineHeight: '1.125rem' }],
        sm: ['0.8125rem', { lineHeight: '1.25rem' }],
        base: ['0.875rem', { lineHeight: '1.375rem' }],
        md: ['1rem', { lineHeight: '1.5rem' }],
        lg: ['1.125rem', { lineHeight: '1.6rem', letterSpacing: '-0.01em' }],
        xl: ['1.375rem', { lineHeight: '1.8rem', letterSpacing: '-0.015em' }],
        '2xl': ['1.75rem', { lineHeight: '2.1rem', letterSpacing: '-0.02em' }],
        '3xl': ['2.25rem', { lineHeight: '2.5rem', letterSpacing: '-0.025em' }],
      },
      borderRadius: {
        none: '0',
        sm: '3px',
        DEFAULT: '5px',
        md: '7px',
        lg: '10px',
      },
      boxShadow: {
        panel: '0 1px 0 0 oklch(1 0 0 / 0.03) inset, 0 8px 24px -12px oklch(0 0 0 / 0.6)',
        lift: '0 12px 32px -12px oklch(0 0 0 / 0.7)',
        'trail-glow': '0 0 0 1px oklch(0.790 0.150 72 / 0.4), 0 0 20px -4px oklch(0.790 0.150 72 / 0.35)',
      },
      zIndex: {
        // Semantic scale. No 999.
        sticky: '10',
        overlay: '20',
        dialog: '30',
        toast: '40',
        tooltip: '50',
      },
      transitionTimingFunction: {
        // Exponential ease-out. No bounce.
        out: 'cubic-bezier(0.22, 1, 0.36, 1)',
        'out-quart': 'cubic-bezier(0.25, 1, 0.5, 1)',
      },
      keyframes: {
        'fade-rise': {
          from: { opacity: '0', transform: 'translateY(4px)' },
          to: { opacity: '1', transform: 'none' },
        },
        'sweep': {
          from: { transform: 'translateX(-100%)' },
          to: { transform: 'translateX(100%)' },
        },
        'pulse-ring': {
          '0%': { opacity: '0.55', transform: 'scale(1)' },
          '100%': { opacity: '0', transform: 'scale(2.2)' },
        },
      },
      animation: {
        'fade-rise': 'fade-rise 220ms cubic-bezier(0.22, 1, 0.36, 1) both',
        sweep: 'sweep 1.4s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'pulse-ring': 'pulse-ring 1.6s cubic-bezier(0.22, 1, 0.36, 1) infinite',
      },
    },
  },
  plugins: [],
};
