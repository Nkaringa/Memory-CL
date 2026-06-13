"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import cytoscape, { type Core, type ElementDefinition, type StylesheetJson } from "cytoscape";
import fcose from "cytoscape-fcose";
import { EyeOff, GitGraph } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { JsonView } from "@/components/ui/json-view";
import { EmptyState } from "@/components/ui/empty-state";
import {
  EDGE_COLOR_DEFAULT,
  EDGE_COLORS,
  GraphHoverCard,
  InspectorRow,
  InspectorShell,
  ViewportControls,
  baseGraphStyles,
  fcoseLayout,
  selectionGraphStyles,
  shortLabel,
} from "@/components/graph/shared";
import { fmtScore } from "@/lib/utils";
import type {
  GraphQueryCandidate,
  GraphQueryEdge,
  McpToolResponse,
} from "@/lib/types";

cytoscape.use(fcose);

type GraphCandidate = GraphQueryCandidate;

/** Last path segment(s) of a qualified name, capped for canvas legibility.
 *  Full identity lives in the hover tooltip + NodeInspector. */
function nodeLabel(c: GraphCandidate, isSeed: boolean): string {
  const full = c.qualified_name ?? c.unit_id.slice(0, 12);
  // Seed keeps two segments ("a.b") for orientation; others just the leaf.
  return shortLabel(full, isSeed ? 2 : 1);
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
      const kindLc = (c.kind ?? "").toLowerCase();
      const isExternal = kindLc.includes("external") ||
        c.unit_id.startsWith("external:");
      const isSeed = (c.depth ?? -1) === 0;
      elements.push({
        data: {
          id: c.unit_id,
          label: nodeLabel(c, isSeed),
          kind: c.kind ?? "?",
          depth: c.depth ?? 0,
          isExternal,
          // Module/file containers read slightly larger so the hierarchy
          // is visible at a glance.
          isModule: kindLc.includes("module") || kindLc.includes("file"),
        },
      });
    }

    if (data.edges.length > 0) {
      // Real directed edges as stored (Phase-2 EDGE_RULES). Endpoints not in
      // the candidate set are skipped defensively.
      const edgeSeen = new Set<string>();
      for (const e of data.edges) {
        if (!seen.has(e.src_id) || !seen.has(e.dst_id)) continue;
        const id = `${e.src_id}-${e.kind}->${e.dst_id}`;
        if (edgeSeen.has(id)) continue;
        edgeSeen.add(id);
        elements.push({
          data: {
            id,
            source: e.src_id,
            target: e.dst_id,
            label: e.kind,
            color: EDGE_COLORS[e.kind] ?? EDGE_COLOR_DEFAULT,
          },
        });
      }
    } else {
      // Fallback for older/degraded backends that don't return `edges`:
      // seed→node reachability projection (NOT literal graph edges).
      const seed = data.candidates.find((c) => (c.depth ?? -1) === 0);
      if (seed) {
        for (const c of data.candidates) {
          if (c.unit_id === seed.unit_id) continue;
          elements.push({
            data: {
              id: `${seed.unit_id}->${c.unit_id}`,
              source: seed.unit_id,
              target: c.unit_id,
              color: EDGE_COLOR_DEFAULT,
            },
          });
        }
      }
    }

    const style: StylesheetJson = [
      ...baseGraphStyles({ externalDimmed }),
      {
        selector: "node[depth = 0]",
        style: {
          "background-color": "#0e9f6e",
          "border-color": "#067a52",
          color: "#ffffff",
          width: 26,
          height: 26,
          "z-index": 100 as never,
        },
      },
      ...selectionGraphStyles,
    ];

    const cy = cytoscape({
      container: containerRef.current,
      elements,
      layout: fcoseLayout("proof"),
      wheelSensitivity: 0.2,
      style,
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
                  {data.edges.length > 0
                    ? "Graph edges as stored (Phase-2 EDGE_RULES)"
                    : "Reachability view — drawn edges are seed→node projections, not literal graph edges."}
                </div>
                <div
                  ref={containerRef}
                  className="w-full h-[420px] rounded-md border border-border bg-bg/40"
                />

                {hovered && (
                  <GraphHoverCard
                    x={hovered.x}
                    y={hovered.y}
                    title={hovered.node.qualified_name ?? hovered.node.unit_id.slice(0, 16)}
                    subtitle={`${hovered.node.kind ?? "?"} · depth ${hovered.node.depth ?? 0}`}
                  />
                )}

                <ViewportControls cyRef={cyRef} />
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
    <InspectorShell
      hasNodes={hasNodes}
      emptyText="Run a query to populate the graph."
      idleText="Click any node to inspect — full identity + score + depth + provenance."
      selected={node !== null}
    >
      {node && (
        <dl className="space-y-2.5 text-xs">
          <InspectorRow label="qualified_name" value={node.qualified_name ?? "—"} mono />
          <InspectorRow label="unit_id" value={node.unit_id} mono />
          <InspectorRow label="kind" value={node.kind ?? "—"} />
          <InspectorRow label="depth from seed" value={String(node.depth ?? 0)} />
          <InspectorRow label="file_path" value={node.file_path ?? "—"} mono />
          <InspectorRow label="channel" value={node.channel} />
          <InspectorRow label="raw_score" value={fmtScore(node.raw_score)} />
        </dl>
      )}
    </InspectorShell>
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

function extract(response: McpToolResponse | null): {
  candidates: GraphCandidate[];
  edges: GraphQueryEdge[];
} {
  if (!response || response.status !== "success") return { candidates: [], edges: [] };
  const raw = (response.data?.candidates ?? []) as unknown[];
  // `edges` is additive (backend ≥ ff56ac0) — absent or [] on older /
  // degraded backends, in which case the star-projection fallback draws.
  const rawEdges = Array.isArray(response.data?.edges)
    ? (response.data.edges as unknown[])
    : [];
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
    edges: rawEdges.flatMap((r) => {
      const o = r as Record<string, unknown>;
      if (typeof o?.src_id !== "string" || typeof o?.dst_id !== "string") return [];
      return [{ src_id: o.src_id, kind: String(o.kind ?? "?"), dst_id: o.dst_id }];
    }),
  };
}
