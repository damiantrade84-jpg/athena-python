/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: ["class"],
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  // Obsidian design-system classes defined in index.css @layer components must
  // survive production purge even before panels adopt them in TSX.
  safelist: [
    'panel-glass',
    'panel-header-title',
    'panel-title',
    'surface-glass',
    'surface-raised',
    'surface-inset',
    'text-gradient',
    'status-pill',
    'label',
    'readout',
    'note',
    'chip',
    'chip-long',
    'chip-short',
    'chip-accent',
    'chip-warning',
    'badge-long',
    'badge-short',
    'badge-gold',
    'badge-neutral',
    'meter',
    'meter-fill',
    'meter-fill-pass',
    'meter-fill-accent',
    'meter-tick',
    'disclosure',
    'glow-gold',
    'glow-gold-sm',
    'glow-long-sm',
    'glow-short-sm',
    'progress-gold',
    'data-grid',
    'focus-ring',
    'animate-glow-gold',
    'animate-glow-long',
    'animate-slide-in',
  ],
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive) / <alpha-value>)",
          foreground: "hsl(var(--destructive-foreground) / <alpha-value>)",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        sidebar: {
          DEFAULT: "hsl(var(--sidebar-background))",
          foreground: "hsl(var(--sidebar-foreground))",
          primary: "hsl(var(--sidebar-primary))",
          "primary-foreground": "hsl(var(--sidebar-primary-foreground))",
          accent: "hsl(var(--sidebar-accent))",
          "accent-foreground": "hsl(var(--sidebar-accent-foreground))",
          border: "hsl(var(--sidebar-border))",
          ring: "hsl(var(--sidebar-ring))",
        },
        /* Blue accent (legacy "gold" token names, remapped to the Obsidian accent) */
        gold: {
          DEFAULT: "hsl(var(--gold))",
          light: "hsl(var(--gold-light))",
          dark: "hsl(var(--gold-dark))",
          muted: "hsl(var(--gold-muted))",
        },
        platinum: "hsl(var(--platinum))",
        /* Semantic — direction and P&L only */
        long: "hsl(var(--long))",
        short: "hsl(var(--short))",
        warning: "hsl(var(--warning))",
        info: "hsl(var(--info))",
        /* Chart categorical ramp — fixed order, never cycled */
        chart: {
          1: "hsl(var(--chart-1))",
          2: "hsl(var(--chart-2))",
          3: "hsl(var(--chart-3))",
          4: "hsl(var(--chart-4))",
          5: "hsl(var(--chart-5))",
          6: "hsl(var(--chart-6))",
        },
      },
      fontFamily: {
        display: ["'Space Grotesk'", "Inter", "system-ui", "sans-serif"],
        body: ["Inter", "system-ui", "sans-serif"],
        mono: ["'JetBrains Mono'", "ui-monospace", "monospace"],
      },
      borderRadius: {
        xl: "calc(var(--radius) + 4px)",
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
        xs: "calc(var(--radius) - 6px)",
      },
      /* Elevation, not luminance. The legacy glow names resolve to real
         drop shadows so `shadow-gold` on un-rebuilt panels reads as depth. */
      boxShadow: {
        xs: "0 1px 2px 0 rgb(0 0 0 / 0.30)",
        gold: "0 4px 16px rgb(0 0 0 / 0.45)",
        "gold-sm": "0 2px 8px rgb(0 0 0 / 0.35)",
        "long-sm": "0 2px 8px rgb(0 0 0 / 0.35)",
        "short-sm": "0 2px 8px rgb(0 0 0 / 0.35)",
        "elev-1": "0 4px 16px hsl(224 45% 3% / 0.40)",
        "elev-2": "0 14px 36px hsl(224 45% 3% / 0.55)",
        "glow-accent": "0 0 22px hsl(216 100% 66% / 0.28)",
        "glow-long": "0 0 22px hsl(152 64% 46% / 0.26)",
        "glow-short": "0 0 22px hsl(358 78% 61% / 0.26)",
      },
      keyframes: {
        "accordion-down": {
          from: { height: "0" },
          to: { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to: { height: "0" },
        },
        "caret-blink": {
          "0%,70%,100%": { opacity: "1" },
          "20%,50%": { opacity: "0" },
        },
        "glow-pulse-gold": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.72" },
        },
        "fade-up": {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
        "caret-blink": "caret-blink 1.25s ease-out infinite",
        "glow-gold": "glow-pulse-gold 2.5s ease-in-out infinite",
        "fade-up": "fade-up 0.28s cubic-bezier(0.22, 1, 0.36, 1) both",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
}