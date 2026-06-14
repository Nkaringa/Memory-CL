"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Check } from "lucide-react";
import { Panel, Btn } from "@/components/shell/primitives";
import { copyToClipboard } from "@/lib/utils";
import {
  listProviderConfigs,
  createProvider,
  setProviderEnabled,
  deleteProvider,
  type CreateProviderBody,
} from "@/lib/auth";
import type { ProviderAdmin } from "@/lib/types";

const PROVIDER_OPTIONS: { label: string; value: string }[] = [
  { label: "GitHub", value: "github" },
  { label: "Google", value: "google" },
  { label: "Microsoft", value: "microsoft" },
  { label: "Generic OIDC", value: "oidc" },
];

function providerTypeLabel(pt: string): string {
  return PROVIDER_OPTIONS.find((o) => o.value === pt)?.label ?? pt;
}

function CallbackUrlDisplay({ providerId }: { providerId: string }) {
  const [copied, setCopied] = useState(false);
  const origin = typeof window !== "undefined" ? window.location.origin : "<origin>";
  const url = `${origin}/api/auth/oauth/${providerId}/callback`;
  return (
    <div className="mt-3 rounded-lg border border-[#c7e9d8] bg-[#f0faf5] px-3.5 py-3">
      <div className="mb-1.5 text-[12.5px] font-semibold text-accentInk">
        Register this callback URL at your provider
      </div>
      <div className="flex items-center gap-2">
        <pre className="flex-1 overflow-x-auto whitespace-pre-wrap break-all rounded-md bg-[#1d1d1b] px-3 py-2 font-mono text-[12px] text-[#e6e6e6]">
          {url}
        </pre>
        <button
          type="button"
          onClick={async () => {
            if (await copyToClipboard(url)) {
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }
          }}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-border2 bg-bg px-2.5 py-1.5 text-[12px] font-medium text-muted2 hover:border-muted hover:text-fg"
        >
          {copied ? <Check size={12} /> : <Copy size={12} />}
          {copied ? "copied" : "copy"}
        </button>
      </div>
      <div className="mt-1.5 text-[11.5px] text-muted">
        Add this as the OAuth redirect/callback URI in your provider&apos;s app settings.
      </div>
    </div>
  );
}

function AddProviderForm({ onCreated }: { onCreated: (p: ProviderAdmin) => void }) {
  const [providerType, setProviderType] = useState("github");
  const [displayName, setDisplayName] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [discoveryUrl, setDiscoveryUrl] = useState("");
  const [scopes, setScopes] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: (body: CreateProviderBody) => createProvider(body),
    onSuccess: (created) => {
      setProviderType("github");
      setDisplayName("");
      setClientId("");
      setClientSecret("");
      setDiscoveryUrl("");
      setScopes("");
      setFormError(null);
      onCreated(created);
    },
    onError: (err) => {
      setFormError(err instanceof Error ? err.message : "Could not create provider");
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    if (providerType === "oidc" && !discoveryUrl.trim()) {
      setFormError("Discovery URL is required for Generic OIDC providers.");
      return;
    }
    const body: CreateProviderBody = {
      provider_type: providerType,
      display_name: displayName.trim(),
      client_id: clientId.trim(),
      client_secret: clientSecret,
    };
    if (discoveryUrl.trim()) body.discovery_url = discoveryUrl.trim();
    if (scopes.trim()) body.scopes = scopes.trim();
    create.mutate(body);
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3 pt-3 border-t border-border">
      <div className="text-[13px] font-semibold">Add provider</div>

      <div className="grid grid-cols-2 gap-2.5">
        <div>
          <label className="mb-1 block text-[11.5px] font-medium text-muted2">Provider type</label>
          <select
            value={providerType}
            onChange={(e) => setProviderType(e.target.value)}
            className="w-full rounded-lg border border-border bg-bg px-3 py-2 text-[13px] outline-none focus:border-accent"
          >
            {PROVIDER_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="mb-1 block text-[11.5px] font-medium text-muted2">Display name</label>
          <input
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="e.g. GitHub"
            required
            className="w-full rounded-lg border border-border bg-bg px-3 py-2 text-[13px] outline-none focus:border-accent"
          />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2.5">
        <div>
          <label className="mb-1 block text-[11.5px] font-medium text-muted2">Client ID</label>
          <input
            type="text"
            value={clientId}
            onChange={(e) => setClientId(e.target.value)}
            placeholder="client id"
            required
            className="w-full rounded-lg border border-border bg-bg px-3 py-2 text-[13px] outline-none focus:border-accent"
          />
        </div>

        <div>
          <label className="mb-1 block text-[11.5px] font-medium text-muted2">
            Client secret{" "}
            <span className="text-[10.5px] font-normal text-muted">(write-only — never shown)</span>
          </label>
          <input
            type="password"
            value={clientSecret}
            onChange={(e) => setClientSecret(e.target.value)}
            placeholder="client secret"
            required
            autoComplete="off"
            className="w-full rounded-lg border border-border bg-bg px-3 py-2 text-[13px] outline-none focus:border-accent"
          />
        </div>
      </div>

      {providerType === "oidc" && (
        <div>
          <label className="mb-1 block text-[11.5px] font-medium text-muted2">
            Discovery URL <span className="text-bad">*</span>
          </label>
          <input
            type="url"
            value={discoveryUrl}
            onChange={(e) => setDiscoveryUrl(e.target.value)}
            placeholder="https://accounts.example.com/.well-known/openid-configuration"
            required
            className="w-full rounded-lg border border-border bg-bg px-3 py-2 text-[13px] outline-none focus:border-accent"
          />
        </div>
      )}

      <div>
        <label className="mb-1 block text-[11.5px] font-medium text-muted2">
          Scopes <span className="text-[10.5px] font-normal text-muted">(optional)</span>
        </label>
        <input
          type="text"
          value={scopes}
          onChange={(e) => setScopes(e.target.value)}
          placeholder="e.g. openid email profile"
          className="w-full rounded-lg border border-border bg-bg px-3 py-2 text-[13px] outline-none focus:border-accent"
        />
      </div>

      {formError && (
        <div className="rounded-lg border border-[#f0c9c9] bg-[#fef2f2] px-3.5 py-2.5 text-[12.5px] text-bad">
          {formError}
        </div>
      )}

      <div>
        <Btn
          primary
          onClick={() => {}}
          className={create.isPending ? "pointer-events-none opacity-50" : ""}
        >
          {create.isPending ? "Adding…" : "Add provider"}
        </Btn>
      </div>
    </form>
  );
}

function ProviderRow({
  provider,
  onToggle,
  onDelete,
}: {
  provider: ProviderAdmin;
  onToggle: () => void;
  onDelete: () => void;
}) {
  const [confirmDelete, setConfirmDelete] = useState(false);

  return (
    <tr className="border-t border-border text-[13px]">
      <td className="py-2.5 font-medium">{provider.display_name}</td>
      <td className="py-2.5 text-muted">{providerTypeLabel(provider.provider_type)}</td>
      <td className="py-2.5 font-mono text-muted">{provider.client_id}</td>
      <td className="py-2.5">
        {provider.has_secret ? (
          <span className="text-accentInk font-medium">set</span>
        ) : (
          <span className="text-muted2">not set</span>
        )}
      </td>
      <td className="py-2.5">
        <button
          type="button"
          onClick={onToggle}
          className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[12px] font-medium transition-colors ${
            provider.enabled
              ? "border-accent bg-accentSoft text-accentInk hover:bg-[#d1f0e3]"
              : "border-border2 bg-bg text-muted2 hover:border-muted hover:text-fg"
          }`}
        >
          {provider.enabled ? "enabled" : "disabled"}
        </button>
      </td>
      <td className="py-2.5 text-right">
        {confirmDelete ? (
          <span className="inline-flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => { onDelete(); setConfirmDelete(false); }}
              className="rounded-md border border-bad bg-[#fef2f2] px-2.5 py-1 text-[12px] font-medium text-bad hover:bg-[#fee2e2]"
            >
              confirm delete
            </button>
            <button
              type="button"
              onClick={() => setConfirmDelete(false)}
              className="rounded-md border border-border2 bg-bg px-2.5 py-1 text-[12px] font-medium text-muted2 hover:border-muted hover:text-fg"
            >
              cancel
            </button>
          </span>
        ) : (
          <button
            type="button"
            onClick={() => setConfirmDelete(true)}
            className="rounded-md border border-border2 bg-bg px-2.5 py-1 text-[12px] font-medium text-muted2 hover:border-bad hover:text-bad"
          >
            delete
          </button>
        )}
      </td>
    </tr>
  );
}

export function IdentityPanel() {
  const qc = useQueryClient();
  const [createdProvider, setCreatedProvider] = useState<ProviderAdmin | null>(null);

  const providers = useQuery({
    queryKey: ["identity-providers"],
    queryFn: listProviderConfigs,
  });

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      setProviderEnabled(id, enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["identity-providers"] }),
  });

  const remove = useMutation({
    mutationFn: (id: string) => deleteProvider(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["identity-providers"] }),
  });

  const list: ProviderAdmin[] = providers.data ?? [];

  return (
    <Panel title="Identity providers" className="mb-3.5">
      <div className="px-4 py-4">
        <div className="mb-3 text-[12.5px] text-muted">
          Configure OAuth providers for federated login. Client secrets are write-only and never
          displayed after creation. After adding a provider, copy the callback URL below and register
          it in your provider&apos;s OAuth app settings.
        </div>

        {providers.isError && (
          <div className="mb-3 rounded-lg border border-[#f0c9c9] bg-[#fef2f2] px-3.5 py-2.5 text-[12.5px] text-bad">
            Could not load identity providers.
          </div>
        )}

        {list.length === 0 && !providers.isError ? (
          <div className="mb-3 text-[12.5px] text-muted2">
            {providers.isLoading ? "Loading…" : "No providers configured yet."}
          </div>
        ) : (
          <div className="mb-4 overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="text-[11.5px] uppercase tracking-wide text-muted">
                  <th className="py-2 text-left font-semibold">display name</th>
                  <th className="py-2 text-left font-semibold">type</th>
                  <th className="py-2 text-left font-semibold">client ID</th>
                  <th className="py-2 text-left font-semibold">secret</th>
                  <th className="py-2 text-left font-semibold">status</th>
                  <th className="py-2 text-right font-semibold"></th>
                </tr>
              </thead>
              <tbody>
                {list.map((p) => (
                  <ProviderRow
                    key={p.id}
                    provider={p}
                    onToggle={() => toggle.mutate({ id: p.id, enabled: !p.enabled })}
                    onDelete={() => remove.mutate(p.id)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}

        {toggle.isError && (
          <div className="mb-2 text-[12px] text-bad">Could not update provider status.</div>
        )}
        {remove.isError && (
          <div className="mb-2 text-[12px] text-bad">Could not delete provider.</div>
        )}

        {createdProvider && (
          <CallbackUrlDisplay providerId={createdProvider.id} />
        )}

        <AddProviderForm
          onCreated={(p) => {
            setCreatedProvider(p);
            qc.invalidateQueries({ queryKey: ["identity-providers"] });
          }}
        />
      </div>
    </Panel>
  );
}
