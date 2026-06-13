"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { getMemoryClient } from "@/lib/api";
import { PageHeader, Panel, Btn } from "@/components/shell/primitives";
import { copyToClipboard } from "@/lib/utils";
import type { EmbeddingMode, FeatureWeightsView } from "@/lib/types";

// Pinned Phase-4 defaults — used when the backend omits feature_weights.
const DEFAULT_WEIGHTS: FeatureWeightsView = {
  semantic: 0.35,
  graph: 0.25,
  recency: 0.2,
  importance: 0.15,
  feedback: 0.05,
};

const WEIGHT_ORDER: (keyof FeatureWeightsView)[] = [
  "semantic",
  "graph",
  "recency",
  "importance",
  "feedback",
];

/** Build the pre-filled `claude mcp add` command for the live origin. */
function connectCmd(origin: string, key: string): string {
  return `claude mcp add --transport sse --scope user \\
  memory-cl ${origin}/mcp/sse \\
  --header "X-API-Key: ${key}"`;
}

export default function SettingsPage() {
  const client = getMemoryClient();
  const status = useQuery({ queryKey: ["status"], queryFn: () => client.status() });

  const s = status.data;
  const weights = s?.feature_weights ?? DEFAULT_WEIGHTS;
  const fromBackend = s?.feature_weights != null;
  const embeddingsOn = s?.embeddings_enabled ?? false;

  return (
    <div className="mx-auto max-w-[1080px]">
      <PageHeader
        title="Settings"
        subtitle="Manage access keys and embeddings; view the live engine configuration."
      />

      <AccessKeysPanel />

      <Panel title="Ranking weights" className="mb-3.5">
        <div className="px-4 py-4">
          <div className="mb-3 text-[12.5px] text-muted">
            How retrieval channels combine into a result score —{" "}
            {fromBackend ? "served live by the engine." : "engine omitted them; showing pinned Phase-4 defaults."}
          </div>
          {WEIGHT_ORDER.map((k) => (
            <div key={k} className="my-1.5 flex items-center gap-3 text-[12.5px]">
              <span className="w-24 text-muted2">{k}</span>
              <span className="h-2 flex-1 overflow-hidden rounded bg-panel2">
                <i
                  className="block h-full rounded bg-accent"
                  style={{ width: `${Math.min(100, weights[k] * 100)}%` }}
                />
              </span>
              <span className="w-12 text-right font-mono tabular-nums text-muted">
                {weights[k].toFixed(2)}
              </span>
            </div>
          ))}
          <div className="mt-2 text-[11.5px] text-muted">
            sum ={" "}
            <span className="font-mono">
              {WEIGHT_ORDER.reduce((a, k) => a + weights[k], 0).toFixed(2)}
            </span>
          </div>
        </div>
      </Panel>

      <Panel title="Connection">
        <div className="px-4 py-2">
          <Row k="server">
            <span className="font-mono">same-origin /api proxy → backend</span>
          </Row>
          <Row k="API key">
            <span className="font-mono text-muted">injected server-side (MCP_API_KEY)</span>
          </Row>
          <Row k="embeddings">
            {embeddingsOn ? "enabled" : "disabled"}
            {s?.service ? ` · ${s.service}` : ""}
          </Row>
          <Row k="environment">{s?.environment ?? "—"}</Row>
          <Row k="schema version">
            <span className="font-mono">{s?.schema_version ?? "—"}</span>
          </Row>
          <Row k="theme">Light (minimal) · emerald accent</Row>
        </div>
      </Panel>

      {status.isError && (
        <div className="mt-3 rounded-xl border border-[#f0c9c9] bg-[#fef2f2] px-4 py-2.5 text-[13px] text-bad">
          Could not load status — showing defaults where possible.
        </div>
      )}
    </div>
  );
}

/** Runtime key + embedding management — all server-side; the browser only ever
 *  sees the one-time reveal on rotate. */
function AccessKeysPanel() {
  const client = getMemoryClient();
  const qc = useQueryClient();

  const config = useQuery({ queryKey: ["config"], queryFn: () => client.getConfig() });
  const cfg = config.data;

  const [confirmRotate, setConfirmRotate] = useState(false);
  const [rotatedKey, setRotatedKey] = useState<string | null>(null);
  const [openAiInput, setOpenAiInput] = useState("");
  const origin = typeof window !== "undefined" ? window.location.origin : "<origin>";

  const rotate = useMutation({
    mutationFn: () => client.rotateMcpKey(),
    onSuccess: (r) => {
      setRotatedKey(r.api_key);
      setConfirmRotate(false);
      qc.invalidateQueries({ queryKey: ["config"] });
    },
  });

  const saveOpenAi = useMutation({
    mutationFn: (key: string | null) => client.setOpenAiKey(key),
    onSuccess: () => {
      setOpenAiInput("");
      qc.invalidateQueries({ queryKey: ["config"] });
    },
  });

  const setMode = useMutation({
    mutationFn: (mode: EmbeddingMode) => client.setEmbeddingMode(mode),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["config"] }),
  });

  return (
    <Panel title="Access &amp; Keys" className="mb-3.5">
      <div className="px-4 py-3.5">
        {/* MCP key */}
        <div className="border-b border-border pb-4">
          <div className="text-[13px] font-semibold">MCP access key</div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-[12.5px] text-muted">
            <span>current:</span>
            <span className="font-mono text-fg">{cfg?.mcp_key_hint ?? "not set"}</span>
          </div>

          {rotatedKey ? (
            <div className="mt-3">
              <div className="mb-2 flex items-center gap-2.5 rounded-lg border border-[#f3e2c0] bg-warnSoft px-3.5 py-2.5 text-[12.5px] text-[#8a5a00]">
                New key — save it now, it won&apos;t be shown again. Agents must be re-added.
              </div>
              <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded-lg bg-[#1d1d1b] px-4 py-3 font-mono text-[12.5px] text-[#e6e6e6]">
                {rotatedKey}
              </pre>
              <div className="mt-2 flex gap-2">
                <CopyBtn text={rotatedKey}>copy key</CopyBtn>
              </div>
              <div className="mt-3 text-[12px] font-semibold text-muted2">Re-add your agent:</div>
              <pre className="mt-1.5 overflow-x-auto whitespace-pre rounded-lg bg-[#1d1d1b] px-4 py-3 font-mono text-[12.5px] text-[#e6e6e6]">
                {connectCmd(origin, rotatedKey)}
              </pre>
              <div className="mt-2 flex gap-2">
                <CopyBtn text={connectCmd(origin, rotatedKey)}>copy command</CopyBtn>
                <Btn onClick={() => setRotatedKey(null)}>done</Btn>
              </div>
            </div>
          ) : confirmRotate ? (
            <div className="mt-3 rounded-lg border border-[#f3e2c0] bg-warnSoft px-3.5 py-3">
              <div className="text-[12.5px] text-[#8a5a00]">
                Regenerating invalidates the current key — every connected agent will need
                re-adding. Continue?
              </div>
              <div className="mt-2.5 flex gap-2">
                <Btn
                  primary
                  onClick={() => rotate.mutate()}
                  className={rotate.isPending ? "pointer-events-none opacity-50" : ""}
                >
                  {rotate.isPending ? "Regenerating…" : "Yes, regenerate"}
                </Btn>
                <Btn onClick={() => setConfirmRotate(false)}>cancel</Btn>
              </div>
              {rotate.isError ? (
                <div className="mt-2 text-[12px] text-bad">
                  Could not rotate the key (the current key may be required).
                </div>
              ) : null}
            </div>
          ) : (
            <div className="mt-2.5">
              <Btn onClick={() => setConfirmRotate(true)}>Regenerate key</Btn>
            </div>
          )}
        </div>

        {/* OpenAI key */}
        <div className="border-b border-border py-4">
          <div className="text-[13px] font-semibold">OpenAI key</div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-[12.5px] text-muted">
            <span>status:</span>
            <span className={cfg?.has_openai_key ? "font-medium text-accentInk" : "text-muted2"}>
              {cfg?.has_openai_key ? "set" : "not set"}
            </span>
          </div>
          <div className="mt-2.5 flex flex-wrap items-center gap-2">
            <input
              type="password"
              value={openAiInput}
              onChange={(e) => setOpenAiInput(e.target.value)}
              placeholder="sk-…"
              className="w-full max-w-[320px] rounded-lg border border-border bg-bg px-3 py-2 font-mono text-[12.5px] outline-none focus:border-accent"
            />
            <Btn
              primary
              onClick={() => saveOpenAi.mutate(openAiInput.trim())}
              className={!openAiInput.trim() || saveOpenAi.isPending ? "pointer-events-none opacity-50" : ""}
            >
              {cfg?.has_openai_key ? "Replace" : "Set key"}
            </Btn>
            {cfg?.has_openai_key ? (
              <Btn
                onClick={() => saveOpenAi.mutate(null)}
                className={saveOpenAi.isPending ? "pointer-events-none opacity-50" : ""}
              >
                clear
              </Btn>
            ) : null}
          </div>
          {saveOpenAi.isError ? (
            <div className="mt-2 text-[12px] text-bad">Could not update the OpenAI key.</div>
          ) : null}
        </div>

        {/* embedding mode */}
        <div className="pt-4">
          <div className="text-[13px] font-semibold">Embedding mode</div>
          <div className="mt-1 text-[12.5px] text-muted">
            current:{" "}
            <span className="font-medium text-fg">{cfg?.embedding_mode ?? "—"}</span>
          </div>
          <div className="mt-2.5 flex gap-2">
            <button
              type="button"
              onClick={() => setMode.mutate("openai")}
              disabled={setMode.isPending}
              className={`rounded-lg border px-3 py-[7px] text-[13px] font-medium transition-colors disabled:opacity-50 ${
                cfg?.embedding_mode === "openai"
                  ? "border-accent bg-accentSoft text-accentInk"
                  : "border-border2 bg-bg text-muted2 hover:border-muted hover:text-fg"
              }`}
            >
              OpenAI
            </button>
            <button
              type="button"
              onClick={() => setMode.mutate("local")}
              disabled={setMode.isPending}
              className={`rounded-lg border px-3 py-[7px] text-[13px] font-medium transition-colors disabled:opacity-50 ${
                cfg?.embedding_mode === "local"
                  ? "border-accent bg-accentSoft text-accentInk"
                  : "border-border2 bg-bg text-muted2 hover:border-muted hover:text-fg"
              }`}
            >
              Local
            </button>
          </div>
          <div className="mt-2 text-[12px] text-muted">
            {setMode.isPending
              ? "Switching mode and re-indexing every repository — this can take a moment…"
              : "Switching mode rebuilds every repo's vectors at the new dimension (OpenAI 1536 · Local 384) and re-embeds them."}
          </div>
          {setMode.data?.reindexed ? (
            <div className="mt-2 text-[12px] font-medium text-accentInk">
              Re-indexed {setMode.data.repos_reindexed} repo
              {setMode.data.repos_reindexed === 1 ? "" : "s"} ·{" "}
              {setMode.data.units_embedded.toLocaleString()} units re-embedded
              {setMode.data.failed_batches > 0
                ? ` · ${setMode.data.failed_batches} batch(es) failed`
                : ""}
            </div>
          ) : null}
          {setMode.isError ? (
            <div className="mt-2 text-[12px] text-bad">Could not change the mode.</div>
          ) : null}
        </div>
      </div>
    </Panel>
  );
}

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between border-b border-border py-2 text-[13px] last:border-0">
      <span className="text-muted2">{k}</span>
      <span>{children}</span>
    </div>
  );
}

function CopyBtn({ text, children }: { text: string; children: React.ReactNode }) {
  const [done, setDone] = useState(false);
  return (
    <button
      onClick={async () => {
        if (await copyToClipboard(text)) {
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        }
      }}
      className="inline-flex items-center gap-1.5 rounded-lg border border-border2 bg-bg px-3 py-1.5 text-[12.5px] font-medium text-muted2 hover:border-muted hover:text-fg"
    >
      {done ? <Check size={13} /> : <Copy size={13} />} {done ? "copied" : children}
    </button>
  );
}
