"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import fcose from "cytoscape-fcose";
import {
  Crosshair, EyeOff, GitGraph, Maximize2, ZoomIn, ZoomOut,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import { JsonView } from "@/components/ui/json-view";
import { EmptyState } from "@/components/ui/empty-state";
import { fmtScore } from "@/lib/utils";
import type { McpToolResponse } from "@/lib/types";

cytoscape.use(fcose);

interface GraphCandidate {
  unit_id: string;
  qualified_name: string | null;
  kind: string | null;
  file_path: string | null;
  raw_score: number;
  channel: string;
  depth: number | null;
}

export interface GraphViewerProps {
  /** Raw MCP response from the `query_graph` tool. */
  response: McpToolResponse | null;
  /** Current depth slider value — owned by the caller so the page can
   *  re-issue the query when the user drags. */
  depth: number;
  onDepthChange: (depth: number) => void;
  className?: string;
}

export function GraphViewer({
  response, depth, onDepthChange, className,
}: GraphViewerProps) {
  const [advanced, setAdvanced] = useState(false);
  const [externalDimmed, setExternalDimmed] = useState(true);
  const [hovered, setHovered] = useState<{ x: number; y: number; node: GraphCandidate } | null>(null);
  const [selected, setSelected] = useState<GraphCandidate | null>(null);

  const data = useMemo(() => extract(response), [response]);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<Core | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    if (cyRef.current) cyRef.current.destroy();

    const elements: ElementDefinition[] = [];
    const seen = new Set<string>();
    for (const c of data.candidates) {
      if (seen.has(c.unit_id)) continue;
      seen.add(c.unit_id);
      const isExternal = (c.kind ?? "").toLowerCase().includes("external") ||
        c.unit_id.startsWith("external:");
      elements.push({
        data: {
          id: c.unit_id,
          label: c.qualified_name ?? c.unit_id.slice(0, 12),
          kind: c.kind ?? "?",
          depth: c.depth ?? 0,
          isExternal,
        },
      });
    }
    const seed = data.candidates.find((c) => (c.depth ?? -1) === 0);
    if (seed) {
      for (const c of data.candidates) {
        if (c.unit_id === seed.unit_id) continue;
        elements.push({
          data: {
            id: `${seed.unit_id}->${c.unit_id}`,
            source: seed.unit_id,
            target: c.unit_id,
          },
        });
      }
    }

    const cy = cytoscape({
      container: containerRef.current,
      elements,
      layout: { name: "fcose", animate: false, randomize: false } as never,
      wheelSensitivity: 0.2,
      style: [
        {
          selector: "node",
          style: {
            "background-color": "#161b22",
            "border-width": 1,
            "border-color": "#30363d",
            label: "data(label)",
            color: "#e6eaf0",
            "font-size": 9,
            "font-family": "ui-monospace, monospace",
            "text-valign": "bottom",
            "text-margin-y": 6,
            width: 18,
            height: 18,
          },
        },
        {
          selector: "node[?isExternal]",
          style: {
            opacity: externalDimmed ? 0.32 : 0.85,
            "background-color": "#30363d",
            "border-style": "dashed",
            "border-color": "#484f58",
            color: "#7d8590",
          },
        },
        {
          selector: "node[depth = 0]",
          style: {
            "background-color": "#58a6ff",
            "border-color": "#58a6ff",
            width: 26,
            height: 26,
            "z-index": 100 as never,
          },
        },
        {
          selector: "edge",
          style: {
            "curve-style": "straight",
            "target-arrow-shape": "triangle",
            "line-color": "#30363d",
            "target-arrow-color": "#30363d",
            width: 1,
          },
        },
        {
          selector: ":selected",
          style: {
            "border-color": "#58a6ff",
            "border-width": 2.5,
          },
        },
      ],
    });

    cy.on("tap", "node", (e) => {
      const id = e.target.id();
      const found = data.candidates.find((c) => c.unit_id === id) ?? null;
      setSelected(found);
    });

    cy.on("mouseover", "node", (e) => {
      const id = e.target.id();
      const found = data.candidates.find((c) => c.unit_id === id);
      if (!found) return;
      const pos = e.target.renderedPosition();
      setHovered({ x: pos.x, y: pos.y, node: found });
    });
    cy.on("mouseout", "node", () => setHovered(null));
    cy.on("pan zoom", () => setHovered(null));

    cyRef.current = cy;
    return () => cy.destroy();
  }, [data, externalDimmed]);

  const externalCount = data.candidates.filter((c) => c.unit_id.startsWith("external:")).length;
  const internalCount = data.candidates.length - externalCount;

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle>Graph viewer</CardTitle>
        <div className="flex items-center gap-3 flex-wrap">
          <Badge variant="muted">{internalCount} internal</Badge>
          {externalCount > 0 && <Badge variant="muted">{externalCount} external</Badge>}
          <Switch
            id="graph-external-dim"
            checked={externalDimmed}
            onCheckedChange={setExternalDimmed}
            label="dim external"
          />
          <Switch
            id="graph-advanced"
            checked={advanced}
            onCheckedChange={setAdvanced}
            label="advanced"
          />
        </div>
      </CardHeader>

      <CardContent className="space-y-3">

        {/* Depth slider — its own row so dragging doesn't fight the controls. */}
        <div className="flex items-center gap-3 rounded-md border border-border bg-bg/30 px-3 py-2">
          <span className="text-xs muted whitespace-nowrap">Depth</span>
          <input
            type="range"
            min={1}
            max={5}
            step={1}
            value={depth}
            onChange={(e) => onDepthChange(parseInt(e.target.value, 10))}
            className="flex-1 accent-accent"
            aria-label="graph traversal depth"
          />
          <span className="font-mono text-xs w-6 text-right">{depth}</span>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-[1fr_300px] gap-3">

          <div className="relative">
            {data.candidates.length === 0 ? (
              <EmptyState
                Icon={GitGraph}
                title="No graph hits"
                description="Submit a node + depth above. EXTERNAL nodes are visually dimmed but still reachable."
                className="h-[420px]"
              />
            ) : data.candidates.length === 1 && (data.candidates[0]?.depth ?? -1) === 0 ? (
              <div className="h-[420px] rounded-md border border-border bg-bg/40 flex items-center justify-center">
                <div className="text-center text-sm muted px-6 max-w-sm">
                  <GitGraph size={24} className="mx-auto mb-3 opacity-40" />
                  Only the seed node was returned — it may have no edges at this depth, or try
                  increasing depth.
                </div>
              </div>
            ) : (
              <>
                <div className="text-[10px] muted font-mono mb-1">
                  Reachability view — drawn edges are seed→node projections, not literal graph edges.
                </div>
                <div
                  ref={containerRef}
                  className="w-full h-[420px] rounded-md border border-border bg-bg/40"
                />

                {/* Hover preview — floats above the canvas. */}
                {hovered && (
                  <div
                    className="absolute pointer-events-none z-20 rounded-md border border-border bg-bg/95 px-2.5 py-1.5 text-[11px] font-mono shadow-lg"
                    style={{ left: hovered.x + 12, top: hovered.y + 12 }}
                  >
                    <div className="text-fg">
                      {hovered.node.qualified_name ?? hovered.node.unit_id.slice(0, 16)}
                    </div>
                    <div className="muted text-[10px]">
                      {hovered.node.kind ?? "?"} · depth {hovered.node.depth ?? 0}
                    </div>
                  </div>
                )}

                {/* Floating viewport controls. */}
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
              </>
            )}
          </div>

          <NodeInspector node={selected} hasNodes={data.candidates.length > 0} />
        </div>

        <Legend />

        {advanced && response && <JsonView value={response} />}
      </CardContent>
    </Card>
  );
}

function NodeInspector({
  node, hasNodes,
}: {
  node: GraphCandidate | null;
  hasNodes: boolean;
}) {
  return (
    <aside className="rounded-md border border-border bg-bg/30 p-3 h-[420px] overflow-auto">
      <div className="text-xs muted mb-3 flex items-center gap-2 uppercase tracking-wider">
        <Crosshair size={12} /> Node inspector
      </div>
      {!hasNodes ? (
        <p className="text-xs muted">Run a query to populate the graph.</p>
      ) : !node ? (
        <p className="text-xs muted">
          Click any node to inspect — full identity + score + depth + provenance.
        </p>
      ) : (
        <dl className="space-y-2.5 text-xs">
          <Row label="qualified_name" value={node.qualified_name ?? "—"} mono />
          <Row label="unit_id" value={node.unit_id} mono />
          <Row label="kind" value={node.kind ?? "—"} />
          <Row label="depth from seed" value={String(node.depth ?? 0)} />
          <Row label="file_path" value={node.file_path ?? "—"} mono />
          <Row label="channel" value={node.channel} />
          <Row label="raw_score" value={fmtScore(node.raw_score)} />
        </dl>
      )}
    </aside>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="space-y-0.5">
      <dt className="text-[10px] muted uppercase tracking-wider">{label}</dt>
      <dd className={mono ? "font-mono break-all" : ""}>{value}</dd>
    </div>
  );
}

function Legend() {
  return (
    <div className="rounded-md border border-border bg-bg/30 p-3 text-xs">
      <div className="muted mb-2 uppercase tracking-wider text-[10px]">Legend</div>
      <ul className="flex flex-wrap gap-x-5 gap-y-1.5 font-mono">
        <li className="flex items-center gap-2">
          <span className="inline-block w-3 h-3 rounded-full bg-accent" /> seed (depth 0)
        </li>
        <li className="flex items-center gap-2">
          <span className="inline-block w-3 h-3 rounded-full bg-panel border border-border" />
          internal node
        </li>
        <li className="flex items-center gap-2">
          <span
            className="inline-block w-3 h-3 rounded-full bg-border opacity-40"
            style={{ borderStyle: "dashed", borderWidth: 1 }}
          />
          EXTERNAL (dimmed)
        </li>
        <li className="flex items-center gap-2 muted">
          <EyeOff size={12} /> Toggle dimming above
        </li>
      </ul>
    </div>
  );
}

function extract(response: McpToolResponse | null): { candidates: GraphCandidate[] } {
  if (!response || response.status !== "success") return { candidates: [] };
  const raw = (response.data?.candidates ?? []) as unknown[];
  return {
    candidates: raw.map((r) => {
      const o = r as Record<string, unknown>;
      return {
        unit_id: String(o.unit_id ?? ""),
        qualified_name: (o.qualified_name as string | null) ?? null,
        kind: (o.kind as string | null) ?? null,
        file_path: (o.file_path as string | null) ?? null,
        raw_score: typeof o.raw_score === "number" ? o.raw_score : 0,
        channel: String(o.channel ?? "graph"),
        depth: typeof o.depth === "number" ? o.depth : null,
      };
    }),
  };
}
