"use client";

import { useEffect, useRef, useState } from "react";
import cytoscape, {
  type Core,
  type ElementDefinition,
  type StylesheetJson,
  type StylesheetJsonBlock,
} from "cytoscape";
import fcose from "cytoscape-fcose";
import { EyeOff, GitGraph, Network } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
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
import type { RepoGraphNode, RepoGraphResponse } from "@/lib/types";

cytoscape.use(fcose);

// Label density thresholds. Above LABEL_ZOOM_THRESHOLD labels only appear
// once zoomed in enough to be legible (cytoscape's min-zoomed-font-size
// culls them cheaply); above LABEL_OFF_THRESHOLD they're off entirely
// except on the selected node — hover still shows the HTML tooltip.
const LABEL_ZOOM_THRESHOLD = 300;
const LABEL_OFF_THRESHOLD = 2500;
// fcose "proof" quality is noticeably better but O(expensive) — drop to
// "default" past this node count so layout stays interactive.
const LAYOUT_QUALITY_THRESHOLD = 800;

export interface RepoGraphViewerProps {
  graph: RepoGraphResponse | null;
  isLoading: boolean;
  /** "Focus BFS here" — the page switches to seed mode with this
   *  qualified_name prefilled and runs the traversal. */
  onFocusBfs: (qualifiedName: string) => void;
  className?: string;
}

export function RepoGraphViewer({
  graph, isLoading, onFocusBfs, className,
}: RepoGraphViewerProps) {
  const [externalDimmed, setExternalDimmed] = useState(true);
  const [hovered, setHovered] = useState<{ x: number; y: number; node: RepoGraphNode } | null>(null);
  const [selected, setSelected] = useState<RepoGraphNode | null>(null);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<Core | null>(null);

  const nodeCount = graph?.nodes.length ?? 0;
  const edgeCount = graph?.edges.length ?? 0;
  const labelsOff = nodeCount > LABEL_OFF_THRESHOLD;
  const labelsZoomGated = !labelsOff && nodeCount > LABEL_ZOOM_THRESHOLD;

  // Selection is per-graph — clear it when the data changes.
  useEffect(() => {
    setSelected(null);
    setHovered(null);
  }, [graph]);

  useEffect(() => {
    if (!containerRef.current || !graph || graph.nodes.length === 0) return;
    if (cyRef.current) cyRef.current.destroy();

    const elements: ElementDefinition[] = [];
    const seen = new Set<string>();
    for (const n of graph.nodes) {
      if (seen.has(n.node_id)) continue;
      seen.add(n.node_id);
      const kindLc = (n.kind ?? "").toLowerCase();
      elements.push({
        data: {
          id: n.node_id,
          label: shortLabel(n.qualified_name || n.name || n.node_id.slice(0, 12)),
          kind: n.kind,
          isExternal: kindLc.includes("external") || n.node_id.startsWith("external:"),
          isModule: kindLc.includes("module") || kindLc.includes("file"),
        },
      });
    }

    // Real directed edges as stored. Endpoints not in the node set are
    // skipped defensively (truncated graphs can dangle).
    const edgeSeen = new Set<string>();
    for (const e of graph.edges) {
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

    const count = graph.nodes.length;
    const off = count > LABEL_OFF_THRESHOLD;
    const zoomGated = !off && count > LABEL_ZOOM_THRESHOLD;

    const style: StylesheetJson = [
      ...baseGraphStyles({ externalDimmed }),
      // Density-dependent label culling.
      ...(off
        ? [{ selector: "node", style: { label: "" } } as StylesheetJsonBlock]
        : zoomGated
          ? [{ selector: "node", style: { "min-zoomed-font-size": 11 } } as StylesheetJsonBlock]
          : []),
      ...selectionGraphStyles,
      // The selected node always shows its label, whatever the density.
      ...(off || zoomGated
        ? [{
            selector: "node:selected",
            style: { label: "data(label)", "min-zoomed-font-size": 0 },
          } as StylesheetJsonBlock]
        : []),
    ];

    const cy = cytoscape({
      container: containerRef.current,
      elements,
      layout: fcoseLayout(count > LAYOUT_QUALITY_THRESHOLD ? "default" : "proof"),
      wheelSensitivity: 0.2,
      style,
    });

    const byId = new Map(graph.nodes.map((n) => [n.node_id, n]));

    cy.on("tap", "node", (e) => {
      setSelected(byId.get(e.target.id()) ?? null);
    });

    cy.on("mouseover", "node", (e) => {
      const found = byId.get(e.target.id());
      if (!found) return;
      const pos = e.target.renderedPosition();
      setHovered({ x: pos.x, y: pos.y, node: found });
    });
    cy.on("mouseout", "node", () => setHovered(null));
    cy.on("pan zoom", () => setHovered(null));

    cyRef.current = cy;
    return () => cy.destroy();
  }, [graph, externalDimmed]);

  const externalCount = graph?.nodes.filter(
    (n) => (n.kind ?? "").toLowerCase().includes("external") || n.node_id.startsWith("external:"),
  ).length ?? 0;

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle>Repo graph</CardTitle>
        <div className="flex items-center gap-3 flex-wrap">
          <Badge variant="muted">{nodeCount} nodes</Badge>
          <Badge variant="muted">{edgeCount} edges</Badge>
          {externalCount > 0 && <Badge variant="muted">{externalCount} external</Badge>}
          {graph?.truncated && (
            <Badge variant="warn">truncated — node cap hit, graph is partial</Badge>
          )}
          {externalCount > 0 && (
            <Switch
              id="repo-graph-external-dim"
              checked={externalDimmed}
              onCheckedChange={setExternalDimmed}
              label="dim external"
            />
          )}
        </div>
      </CardHeader>

      <CardContent className="space-y-3">
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_300px] gap-3">

          <div className="relative">
            {isLoading ? (
              <div className="h-[420px] rounded-md border border-border bg-bg/40 flex items-center justify-center">
                <div className="text-center text-sm muted px-6 max-w-sm">
                  <Network size={24} className="mx-auto mb-3 opacity-40 animate-pulse" />
                  Loading whole-repo graph…
                </div>
              </div>
            ) : !graph || graph.nodes.length === 0 ? (
              <EmptyState
                Icon={GitGraph}
                title="No graph"
                description="Select a repo above — the whole-repo graph loads automatically. External nodes are excluded unless toggled on."
                className="h-[420px]"
              />
            ) : (
              <>
                <div className="text-[10px] muted font-mono mb-1">
                  {labelsOff
                    ? "large graph — labels on hover"
                    : labelsZoomGated
                      ? "Whole-repo graph as stored — zoom in for labels"
                      : "Whole-repo graph as stored (Phase-2 EDGE_RULES)"}
                </div>
                <div
                  ref={containerRef}
                  className="w-full h-[420px] rounded-md border border-border bg-bg/40"
                />

                {hovered && (
                  <GraphHoverCard
                    x={hovered.x}
                    y={hovered.y}
                    title={hovered.node.qualified_name || hovered.node.name}
                    subtitle={`${hovered.node.kind} · ${hovered.node.file_path ?? "—"}`}
                  />
                )}

                <ViewportControls cyRef={cyRef} />
              </>
            )}
          </div>

          <RepoNodeInspector
            node={selected}
            hasNodes={nodeCount > 0}
            onFocusBfs={onFocusBfs}
          />
        </div>

        <Legend />
      </CardContent>
    </Card>
  );
}

function RepoNodeInspector({
  node, hasNodes, onFocusBfs,
}: {
  node: RepoGraphNode | null;
  hasNodes: boolean;
  onFocusBfs: (qualifiedName: string) => void;
}) {
  return (
    <InspectorShell
      hasNodes={hasNodes}
      emptyText="Load a repo graph to populate."
      idleText="Click any node to inspect — identity, kind, file + line range."
      selected={node !== null}
    >
      {node && (
        <>
          <dl className="space-y-2.5 text-xs">
            <InspectorRow label="qualified_name" value={node.qualified_name || "—"} mono />
            <InspectorRow label="node_id" value={node.node_id} mono />
            <InspectorRow label="kind" value={node.kind} />
            <InspectorRow label="name" value={node.name || "—"} mono />
            <InspectorRow label="file_path" value={node.file_path ?? "—"} mono />
            <InspectorRow
              label="lines"
              value={node.line_start != null ? `${node.line_start}–${node.line_end ?? "?"}` : "—"}
            />
          </dl>
          <Button
            size="sm"
            variant="secondary"
            className="mt-3 w-full"
            disabled={!node.qualified_name}
            onClick={() => onFocusBfs(node.qualified_name)}
          >
            <GitGraph size={13} className="mr-1.5" /> Focus BFS here
          </Button>
        </>
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
          <span className="inline-block w-3 h-3 rounded-full bg-panel border-2 border-muted" />
          module / file
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
          <EyeOff size={12} /> Click a node, then “Focus BFS here” to drill in
        </li>
      </ul>
    </div>
  );
}
