import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: "rgb(var(--bg) / <alpha-value>)",
        fg: "rgb(var(--fg) / <alpha-value>)",
        muted: "rgb(var(--muted) / <alpha-value>)",
        muted2: "rgb(var(--muted2) / <alpha-value>)",
        panel: "rgb(var(--panel) / <alpha-value>)",
        panel2: "rgb(var(--panel2) / <alpha-value>)",
        border: "rgb(var(--border) / <alpha-value>)",
        border2: "rgb(var(--border2) / <alpha-value>)",
        accent: "rgb(var(--accent) / <alpha-value>)",
        accentSoft: "rgb(var(--accent-soft) / <alpha-value>)",
        accentInk: "rgb(var(--accent-ink) / <alpha-value>)",
        ok: "rgb(var(--ok) / <alpha-value>)",
        warn: "rgb(var(--warn) / <alpha-value>)",
        warnSoft: "rgb(var(--warn-soft) / <alpha-value>)",
        bad: "rgb(var(--bad) / <alpha-value>)",
      },
      fontFamily: {
        sans: [
          "ui-sans-serif", "system-ui", "-apple-system",
          "Segoe UI", "Roboto", "sans-serif",
        ],
        mono: [
          "ui-monospace", "SF Mono", "Menlo",
          "Consolas", "Liberation Mono", "monospace",
        ],
      },
    },
  },
  plugins: [],
};
export default config;
