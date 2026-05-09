"use client";

import { type FormEvent, useState } from "react";
import { Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";

export interface QueryBoxValue {
  text: string;
  repo_id: string;
  top_k: number;
  channels: { vector: boolean; graph: boolean; metadata: boolean };
  seedUnitIds: string[];
  unitKinds: string[];
}

export interface QueryBoxProps {
  defaultValue?: Partial<QueryBoxValue>;
  onSubmit: (value: QueryBoxValue) => void;
  pending?: boolean;
  className?: string;
}

const UNIT_KIND_OPTIONS = ["mod", "cls", "fn", "mth", "const"];

export function QueryBox({ defaultValue, onSubmit, pending, className }: QueryBoxProps) {
  const [text, setText] = useState(defaultValue?.text ?? "");
  const [repoId, setRepoId] = useState(defaultValue?.repo_id ?? "acme");
  const [topK, setTopK] = useState(defaultValue?.top_k ?? 5);
  const [seedRaw, setSeedRaw] = useState(
    (defaultValue?.seedUnitIds ?? []).join("\n"),
  );
  const [unitKinds, setUnitKinds] = useState<string[]>(defaultValue?.unitKinds ?? []);
  const [channels, setChannels] = useState({
    vector: defaultValue?.channels?.vector ?? true,
    graph: defaultValue?.channels?.graph ?? true,
    metadata: defaultValue?.channels?.metadata ?? true,
  });

  function toggleKind(k: string) {
    setUnitKinds((prev) =>
      prev.includes(k) ? prev.filter((x) => x !== k) : [...prev, k].sort(),
    );
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    onSubmit({
      text: text.trim(),
      repo_id: repoId.trim(),
      top_k: topK,
      channels,
      seedUnitIds: seedRaw
        .split(/[\n,]+/)
        .map((s) => s.trim())
        .filter(Boolean),
      unitKinds,
    });
  }

  return (
    <form
      onSubmit={submit}
      className={`grid grid-cols-1 lg:grid-cols-[1fr_auto] gap-4 ${className ?? ""}`}
    >
      <div className="space-y-3">
        <label className="block text-xs muted">query</label>
        <Textarea
          required
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder='e.g. "auth flow"'
          rows={3}
        />

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div>
            <label className="block text-xs muted mb-1">repo_id</label>
            <Input
              required
              value={repoId}
              onChange={(e) => setRepoId(e.target.value)}
            />
          </div>
          <div>
            <label className="block text-xs muted mb-1">top_k</label>
            <Input
              type="number"
              min={1}
              max={50}
              value={topK}
              onChange={(e) => setTopK(parseInt(e.target.value || "1", 10))}
            />
          </div>
          <div>
            <label className="block text-xs muted mb-1">seed unit_ids</label>
            <Input
              value={seedRaw}
              onChange={(e) => setSeedRaw(e.target.value)}
              placeholder="optional, comma or newline separated"
            />
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-4 pt-2">
          <div className="flex items-center gap-3">
            <Switch
              id="ch-vector"
              checked={channels.vector}
              onCheckedChange={(v) => setChannels((p) => ({ ...p, vector: v }))}
              label="vector"
            />
            <Switch
              id="ch-graph"
              checked={channels.graph}
              onCheckedChange={(v) => setChannels((p) => ({ ...p, graph: v }))}
              label="graph"
            />
            <Switch
              id="ch-metadata"
              checked={channels.metadata}
              onCheckedChange={(v) => setChannels((p) => ({ ...p, metadata: v }))}
              label="metadata"
            />
          </div>
          <div className="flex items-center gap-2">
            {UNIT_KIND_OPTIONS.map((k) => (
              <button
                key={k}
                type="button"
                onClick={() => toggleKind(k)}
                className={`text-[10px] px-2 py-1 rounded font-mono border transition-colors ${
                  unitKinds.includes(k)
                    ? "bg-accent/10 text-accent border-accent/30"
                    : "bg-panel text-muted border-border hover:border-accent/40"
                }`}
              >
                {k}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="flex items-end">
        <Button type="submit" size="lg" disabled={pending}>
          <Search size={16} />
          {pending ? "Retrieving…" : "Retrieve"}
        </Button>
      </div>
    </form>
  );
}
