"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { getMemoryClient } from "@/lib/api";
import { PageHeader, Panel, Btn } from "@/components/shell/primitives";
import { copyToClipboard } from "@/lib/utils";

const STEPS = ["Access key", "Embeddings", "Connect", "First repo"] as const;

/** Build the pre-filled `claude mcp add` command for the live origin. */
function connectCmd(origin: string, key: string): string {
  return `claude mcp add --transport sse --scope user \\
  memory-cl ${origin}/mcp/sse \\
  --header "X-API-Key: ${key}"`;
}

export default function SetupPage() {
  const client = getMemoryClient();
  const qc = useQueryClient();
  const router = useRouter();

  const config = useQuery({ queryKey: ["config"], queryFn: () => client.getConfig() });

  const [step, setStep] = useState(0);
  const [generatedKey, setGeneratedKey] = useState<string | null>(null);
  const [openAiKey, setOpenAiKey] = useState("");
  const [origin, setOrigin] = useState("");

  useEffect(() => {
    if (typeof window !== "undefined") setOrigin(window.location.origin);
  }, []);

  const alreadyConfigured = config.data?.configured ?? false;

  const generate = useMutation({
    mutationFn: () => client.generateMcpKey(),
    onSuccess: (r) => {
      setGeneratedKey(r.api_key);
      qc.invalidateQueries({ queryKey: ["config"] });
    },
  });

  const saveOpenAi = useMutation({
    mutationFn: async () => {
      await client.setOpenAiKey(openAiKey.trim());
      await client.setEmbeddingMode("openai");
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["config"] }),
  });

  const useLocal = useMutation({
    mutationFn: () => client.setEmbeddingMode("local"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["config"] }),
  });

  const mode = config.data?.embedding_mode;
  const localSelected = mode === "local" && (config.data?.embeddings_enabled ?? false);

  const finish = useMutation({
    mutationFn: () => client.completeOnboarding(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["config"] });
      router.push("/");
    },
  });

  // The key used in the connect command: the one just generated, else the
  // masked hint when the system is already configured.
  const connectKey = generatedKey ?? "<your X-API-Key>";

  return (
    <div className="mx-auto max-w-[760px]">
      <PageHeader
        title="Welcome to Memory-CL"
        subtitle="A short setup — generate a key, pick embeddings, connect an agent."
      />

      {/* progress */}
      <div className="mb-5 flex items-center gap-2">
        {STEPS.map((label, i) => (
          <div key={label} className="flex flex-1 items-center gap-2">
            <span
              className={`flex h-6 w-6 flex-none items-center justify-center rounded-full text-[11px] font-bold ${
                i === step
                  ? "bg-accent text-white"
                  : i < step
                    ? "bg-accentSoft text-accentInk"
                    : "bg-panel2 text-muted"
              }`}
            >
              {i < step ? <Check size={13} /> : i + 1}
            </span>
            <span
              className={`hidden text-[12px] font-medium sm:inline ${
                i === step ? "text-fg" : "text-muted"
              }`}
            >
              {label}
            </span>
            {i < STEPS.length - 1 ? (
              <span className="ml-1 hidden h-px flex-1 bg-border sm:block" />
            ) : null}
          </div>
        ))}
      </div>

      {/* ---- Step 1: access key ---- */}
      {step === 0 && (
        <Panel title="Create your access key">
          <div className="px-4 py-4">
            {alreadyConfigured && !generatedKey ? (
              <div className="rounded-lg border border-[#f3e2c0] bg-warnSoft px-3.5 py-3 text-[12.5px] text-[#8a5a00]">
                A key is already set on this instance. You can regenerate it later in{" "}
                <b>Settings → Access &amp; Keys</b>. Continue to finish connecting.
              </div>
            ) : generatedKey ? (
              <>
                <div className="mb-3 flex items-center gap-2.5 rounded-lg border border-[#f3e2c0] bg-warnSoft px-3.5 py-2.5 text-[12.5px] text-[#8a5a00]">
                  Save this now — it won&apos;t be shown again.
                </div>
                <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded-lg bg-[#1d1d1b] px-4 py-3.5 font-mono text-[12.5px] text-[#e6e6e6]">
                  {generatedKey}
                </pre>
                <div className="mt-3">
                  <CopyBtn text={generatedKey}>copy key</CopyBtn>
                </div>
              </>
            ) : (
              <>
                <div className="mb-3 text-[12.5px] text-muted">
                  This X-API-Key authenticates your agents. We&apos;ll show it once — copy it
                  somewhere safe.
                </div>
                <Btn primary onClick={() => generate.mutate()}>
                  {generate.isPending ? "Generating…" : "Generate key"}
                </Btn>
                {generate.isError ? (
                  <div className="mt-3 text-[12.5px] text-bad">
                    Could not generate a key. Is the backend reachable?
                  </div>
                ) : null}
              </>
            )}
          </div>
        </Panel>
      )}

      {/* ---- Step 2: embeddings ---- */}
      {step === 1 && (
        <Panel title="Embeddings">
          <div className="px-4 py-4">
            <div className="mb-3.5 text-[12.5px] text-muted">
              Embeddings power semantic search. Pick a provider, or skip and set it later.
            </div>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              {/* local — on-device, no key */}
              <div
                className={`rounded-xl border bg-bg px-4 py-3.5 ${
                  localSelected ? "border-accent ring-1 ring-accentSoft" : "border-border2"
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className="text-[13.5px] font-semibold text-accentInk">Local</span>
                  <span className="rounded-md bg-accentSoft px-1.5 py-0.5 text-[10px] font-bold text-accentInk">
                    FREE · OFFLINE
                  </span>
                </div>
                <div className="mt-1 text-[12px] text-muted">
                  On-device embeddings (bge-small, 384-dim). No API key. Downloads a ~130 MB
                  model on first use.
                </div>
                <div className="mt-2.5 flex items-center gap-2">
                  <Btn
                    primary
                    onClick={() => useLocal.mutate()}
                    className={useLocal.isPending || localSelected ? "pointer-events-none opacity-50" : ""}
                  >
                    {useLocal.isPending
                      ? "Switching…"
                      : localSelected
                        ? "Selected ✓"
                        : "Use local embeddings"}
                  </Btn>
                </div>
                {useLocal.isError ? (
                  <div className="mt-2 text-[12px] text-bad">Could not switch to local.</div>
                ) : null}
              </div>

              {/* openai */}
              <div className="rounded-xl border border-border2 bg-bg px-4 py-3.5">
                <div className="flex items-center gap-2">
                  <span className="text-[13.5px] font-semibold text-accentInk">OpenAI</span>
                  <span className="rounded-md bg-accentSoft px-1.5 py-0.5 text-[10px] font-bold text-accentInk">
                    BEST QUALITY
                  </span>
                </div>
                <div className="mt-1 text-[12px] text-muted">
                  Paste an OpenAI API key to enable hosted embeddings.
                </div>
                <input
                  type="password"
                  value={openAiKey}
                  onChange={(e) => setOpenAiKey(e.target.value)}
                  placeholder="sk-…"
                  className="mt-2.5 w-full rounded-lg border border-border bg-bg px-3 py-2 font-mono text-[12.5px] outline-none focus:border-accent"
                />
                <div className="mt-2.5 flex items-center gap-2">
                  <Btn
                    primary
                    onClick={() => saveOpenAi.mutate()}
                    className={!openAiKey.trim() || saveOpenAi.isPending ? "pointer-events-none opacity-50" : ""}
                  >
                    {saveOpenAi.isPending ? "Saving…" : "Save key"}
                  </Btn>
                  {saveOpenAi.isSuccess ? (
                    <span className="text-[12px] font-medium text-accentInk">saved ✓</span>
                  ) : null}
                </div>
                {saveOpenAi.isError ? (
                  <div className="mt-2 text-[12px] text-bad">Could not save the key.</div>
                ) : null}
              </div>
            </div>
            <div className="mt-3 text-[12px] text-muted">
              You can skip this for now and configure embeddings later in Settings.
            </div>
          </div>
        </Panel>
      )}

      {/* ---- Step 3: connect ---- */}
      {step === 2 && (
        <Panel title="Connect your agent">
          <div className="px-4 py-4">
            <div className="mb-3 text-[12.5px] text-muted">
              Run this once in Claude Code — it uses SSE and the key you just created. Works in
              every session afterward.
            </div>
            <pre className="overflow-x-auto whitespace-pre rounded-lg bg-[#1d1d1b] px-4 py-3.5 font-mono text-[12.5px] text-[#e6e6e6]">
              {connectCmd(origin || "<origin>", connectKey)}
            </pre>
            <div className="mt-3 flex items-center gap-2">
              <CopyBtn text={connectCmd(origin || "<origin>", connectKey)}>copy command</CopyBtn>
            </div>
            {!generatedKey ? (
              <div className="mt-3 text-[12px] text-muted">
                The command shows a placeholder for the key — use the key you generated (or
                regenerate in Settings).
              </div>
            ) : null}
          </div>
        </Panel>
      )}

      {/* ---- Step 4: first repo ---- */}
      {step === 3 && (
        <Panel title="Add your first repo">
          <div className="px-4 py-4">
            <div className="mb-3.5 text-[12.5px] text-muted">
              Memory-CL builds a code-aware memory by ingesting a repository. Add one now, or
              finish and do it later from the Repositories page.
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Btn href="/repositories">Go to Repositories</Btn>
              <Btn
                primary
                onClick={() => finish.mutate()}
                className={finish.isPending ? "pointer-events-none opacity-50" : ""}
              >
                {finish.isPending ? "Finishing…" : "Finish setup"}
              </Btn>
            </div>
            {finish.isError ? (
              <div className="mt-3 text-[12px] text-bad">
                Could not complete onboarding. Try again.
              </div>
            ) : null}
          </div>
        </Panel>
      )}

      {/* nav */}
      <div className="mt-4 flex items-center">
        <Btn
          onClick={() => setStep((s) => Math.max(0, s - 1))}
          className={step === 0 ? "pointer-events-none opacity-40" : ""}
        >
          ← Back
        </Btn>
        {step < STEPS.length - 1 ? (
          <Btn primary className="ml-auto" onClick={() => setStep((s) => Math.min(STEPS.length - 1, s + 1))}>
            Next →
          </Btn>
        ) : null}
      </div>
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
