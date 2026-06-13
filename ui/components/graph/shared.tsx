"use client";

/* Shared building blocks for the two cytoscape graph surfaces:
 *
 *   - GraphViewer      — BFS-from-seed results (query_graph MCP tool)
 *   - RepoGraphViewer  — whole-repo graph (GET /repos/{id}/graph)
 *
 * Both render the same node/edge visual language (GitHub-dark tokens,
 * kind-colored edges, dimmed EXTERNAL nodes), so the constants, base
 * stylesheet, viewport controls, hover card and inspector chrome live
 * here. Mode-specific styling (seed highlight, label density rules)
 * stays in each component.
 */

import { type ReactNode } from "react";
import type { Core, StylesheetJson } from "cytoscape";
import { Crosshair, Maximize2, ZoomIn, ZoomOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";

// Edge colors by relationship kind — light-theme tokens (emerald accent
// for the semantically interesting CALLS edges, muted grays for structural
// ones) so the graph reads correctly on the white command-center canvas.
export const EDGE_COLOR_DEFAULT = "#d4d4d2";
export const EDGE_COLORS: Record<string, string> = {
  CALLS: "#0e9f6e",    // emerald accent — the interesting edges
  IMPORTS: "#7aa2c4",  // muted blue
  DEFINES: "#d0d0ce",  // faint gray, barely above the border tone
  CONTAINS: "#d0d0ce", // faint gray — structural, not semantic
  INHERITS: "#9b6ef0", // violet — type hierarchy
  REFERENCES: "#d0d0ce", // faint gray, same as DEFINES/CONTAINS
};

/** Last path segment of a qualified name, capped for canvas legibility.
 *  `segments` > 1 keeps trailing context ("a.b") for orientation. */
export function shortLabel(full: string, segments = 1, max = 24): string {
  const parts = full.split(".");
  const short = parts.slice(-segments).join(".") || full;
  return short.length > max ? `${short.slice(0, max - 1)}…` : short;
}

/** fcose layout options shared by both viewers. Option names verified
 *  against cytoscape-fcose/src/fcose/index.js (the package ships no
 *  .d.ts, hence the `never` cast at the call sites): quality, randomize,
 *  packComponents, nodeRepulsion, idealEdgeLength, nodeSeparation.
 *  packComponents fully engages only when cytoscape-layout-utilities is
 *  registered; without it fcose degrades gracefully. */
export function fcoseLayout(quality: "proof" | "default") {
  return {
    name: "fcose",
    animate: false,
    quality,
    randomize: true, // cold layouts need it — `false` caused the smear
    packComponents: true,
    nodeRepulsion: 12000,
    idealEdgeLength: 90,
    nodeSeparation: 120,
  } as never;
}

/** Base node + edge styles shared by both viewers. Selection styles are
 *  separate (`selectionGraphStyles`) so callers can splice mode-specific
 *  selectors (seed highlight, label density) in between at the right
 *  cascade position. */
export function baseGraphStyles({ externalDimmed }: { externalDimmed: boolean }): StylesheetJson {
  return [
    {
      selector: "node",
      style: {
        "background-color": "#ffffff",
        "border-width": 1.5,
        "border-color": "#0e9f6e",
        label: "data(label)",
        color: "#1d1d1b",
        "font-size": 10,
        "font-family": "ui-monospace, monospace",
        "text-valign": "bottom",
        "text-margin-y": 6,
        "text-wrap": "ellipsis",
        "text-max-width": "140px",
        width: 18,
        height: 18,
      },
    },
    {
      // Module/file containers — slightly larger with a distinct solid
      // border so the structural skeleton reads through the leaf nodes.
      selector: "node[?isModule]",
      style: {
        width: 24,
        height: 24,
        "border-width": 2,
        "border-color": "#90908b",
        "background-color": "#f6f6f4",
      },
    },
    {
      selector: "node[?isExternal]",
      style: {
        opacity: externalDimmed ? 0.4 : 0.9,
        "background-color": "#f6f6f4",
        "border-style": "dashed",
        "border-color": "#c4c4c2",
        color: "#90908b",
      },
    },
    {
      selector: "edge",
      style: {
        // bezier so parallel edges of different kinds (CALLS + DEFINES
        // between the same pair) don't overlap into one line.
        "curve-style": "bezier",
        "target-arrow-shape": "triangle",
        "arrow-scale": 0.6,
        "line-color": "data(color)",
        "target-arrow-color": "data(color)",
        width: 1,
      },
    },
  ];
}

/** Selection styles — appended after any mode-specific selectors so
 *  selection always wins the cascade. */
export const selectionGraphStyles: StylesheetJson = [
  {
    // Edge kind label only when selected — never statically (clutter).
    selector: "edge[label]:selected",
    style: {
      label: "data(label)",
      "font-size": 8,
      "font-family": "ui-monospace, monospace",
      color: "#62625e",
      "text-rotation": "autorotate",
      "text-background-color": "#ffffff",
      "text-background-opacity": 0.92,
      "text-background-padding": "2px",
    },
  },
  {
    selector: "edge:selected",
    style: {
      width: 1.5,
    },
  },
  {
    selector: "node:selected",
    style: {
      "border-color": "#067a52",
      "border-width": 3,
    },
  },
];

/** Floating zoom / fit / reset buttons overlaid on the canvas. */
export function ViewportControls({ cyRef }: { cyRef: { current: Core | null } }) {
  return (
    <div className="absolute top-2 right-2 flex flex-col gap-1 rounded-md border border-border bg-bg/80 backdrop-blur-sm p-1">
      <Tooltip content="Zoom in" side="left">
        <Button
          size="sm" variant="ghost"
          onClick={() => cyRef.current?.zoom(cyRef.current.zoom() * 1.25)}
          className="h-7 w-7 p-0" aria-label="zoom in"
        >
          <ZoomIn size={14} />
        </Button>
      </Tooltip>
      <Tooltip content="Zoom out" side="left">
        <Button
          size="sm" variant="ghost"
          onClick={() => cyRef.current?.zoom(cyRef.current.zoom() * 0.8)}
          className="h-7 w-7 p-0" aria-label="zoom out"
        >
          <ZoomOut size={14} />
        </Button>
      </Tooltip>
      <Tooltip content="Fit to viewport" side="left">
        <Button
          size="sm" variant="ghost"
          onClick={() => cyRef.current?.fit(undefined, 30)}
          className="h-7 w-7 p-0" aria-label="fit"
        >
          <Maximize2 size={14} />
        </Button>
      </Tooltip>
      <Tooltip content="Reset zoom + center" side="left">
        <Button
          size="sm" variant="ghost"
          onClick={() => {
            if (!cyRef.current) return;
            cyRef.current.zoom(1);
            cyRef.current.center();
          }}
          className="h-7 w-7 p-0" aria-label="reset zoom"
        >
          <Crosshair size={14} />
        </Button>
      </Tooltip>
    </div>
  );
}

/** Hover preview — floats above the canvas at rendered node position. */
export function GraphHoverCard({
  x, y, title, subtitle,
}: {
  x: number; y: number; title: string; subtitle: string;
}) {
  return (
    <div
      className="absolute pointer-events-none z-20 rounded-md border border-border bg-bg/95 px-2.5 py-1.5 text-[11px] font-mono shadow-lg"
      style={{ left: x + 12, top: y + 12 }}
    >
      <div className="text-fg">{title}</div>
      <div className="muted text-[10px]">{subtitle}</div>
    </div>
  );
}

/** Inspector panel chrome — empty / idle / selected states. Callers
 *  render their own field rows (the two graph shapes differ). */
export function InspectorShell({
  hasNodes, emptyText, idleText, selected, children,
}: {
  hasNodes: boolean;
  emptyText: string;
  idleText: string;
  selected: boolean;
  children?: ReactNode;
}) {
  return (
    <aside className="rounded-md border border-border bg-bg/30 p-3 h-[420px] overflow-auto">
      <div className="text-xs muted mb-3 flex items-center gap-2 uppercase tracking-wider">
        <Crosshair size={12} /> Node inspector
      </div>
      {!hasNodes ? (
        <p className="text-xs muted">{emptyText}</p>
      ) : !selected ? (
        <p className="text-xs muted">{idleText}</p>
      ) : (
        children
      )}
    </aside>
  );
}

export function InspectorRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="space-y-0.5">
      <dt className="text-[10px] muted uppercase tracking-wider">{label}</dt>
      <dd className={mono ? "font-mono break-all" : ""}>{value}</dd>
    </div>
  );
}
