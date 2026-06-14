"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { fetchMe, fetchProviders, login, register } from "@/lib/auth";
import type { ProviderPublic } from "@/lib/types";

export default function LoginPage() {
  const router = useRouter();

  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [checking, setChecking] = useState(true);
  const [providers, setProviders] = useState<ProviderPublic[]>([]);

  // If already authenticated, bounce to the home page immediately.
  // Also load enabled OAuth providers for the social login buttons.
  useEffect(() => {
    let cancelled = false;
    Promise.all([fetchMe(), fetchProviders()]).then(([me, provs]) => {
      if (cancelled) return;
      if (me.authenticated) {
        router.replace("/");
      } else {
        setChecking(false);
        setProviders(provs);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [router]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      if (mode === "login") {
        await login(email, password);
      } else {
        await register(email, password, displayName);
      }
      router.replace("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setSubmitting(false);
    }
  }

  if (checking) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg">
        <span className="text-[13px] text-muted">Loading…</span>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg px-4">
      <div className="w-full max-w-[400px]">
        {/* Logo / brand */}
        <div className="mb-8 flex flex-col items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-accent to-emerald-400 shadow-sm" />
          <div className="text-center">
            <div className="text-[19px] font-semibold tracking-tight">Memory-CL</div>
            <div className="mt-0.5 text-[12.5px] text-muted">Command Center</div>
          </div>
        </div>

        {/* Card */}
        <div className="overflow-hidden rounded-xl border border-border bg-bg shadow-sm">
          {/* Header */}
          <div className="border-b border-border px-6 py-4">
            <div className="text-[14px] font-semibold">
              {mode === "login" ? "Sign in to your account" : "Create an account"}
            </div>
          </div>

          {/* OAuth provider buttons */}
          {providers.length > 0 && (
            <div className="px-6 pt-5 pb-1 space-y-2">
              {providers.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => {
                    window.location.href = `/api/auth/oauth/${p.id}/start`;
                  }}
                  className="w-full rounded-lg border border-border bg-bg px-4 py-2.5 text-[13.5px] font-medium text-fg transition-colors hover:border-accent hover:text-accentInk"
                >
                  Continue with {p.display_name}
                </button>
              ))}
              <div className="flex items-center gap-3 pt-2 pb-1">
                <hr className="flex-1 border-border" />
                <span className="text-[11.5px] text-muted">or</span>
                <hr className="flex-1 border-border" />
              </div>
            </div>
          )}

          {/* Form */}
          <form onSubmit={handleSubmit} className="px-6 py-5 space-y-3">
            {mode === "register" && (
              <div>
                <label className="mb-1 block text-[11.5px] font-medium text-muted2">
                  Display name
                </label>
                <input
                  type="text"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  placeholder="Your name"
                  required
                  autoComplete="name"
                  className="w-full rounded-lg border border-border bg-bg px-3 py-2 text-[13px] outline-none focus:border-accent"
                />
              </div>
            )}

            <div>
              <label className="mb-1 block text-[11.5px] font-medium text-muted2">
                Email
              </label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                required
                autoComplete={mode === "login" ? "username" : "email"}
                className="w-full rounded-lg border border-border bg-bg px-3 py-2 text-[13px] outline-none focus:border-accent"
              />
            </div>

            <div>
              <label className="mb-1 block text-[11.5px] font-medium text-muted2">
                Password
              </label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                required
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                className="w-full rounded-lg border border-border bg-bg px-3 py-2 text-[13px] outline-none focus:border-accent"
              />
            </div>

            {error ? (
              <div className="rounded-lg border border-[#f0c9c9] bg-[#fef2f2] px-3.5 py-2.5 text-[12.5px] text-bad">
                {error}
              </div>
            ) : null}

            <button
              type="submit"
              disabled={submitting}
              className="mt-1 w-full rounded-lg bg-accent px-4 py-2.5 text-[13.5px] font-semibold text-white transition-colors hover:bg-accentInk disabled:opacity-60"
            >
              {submitting
                ? mode === "login"
                  ? "Signing in…"
                  : "Creating account…"
                : mode === "login"
                  ? "Sign in"
                  : "Create account"}
            </button>
          </form>

          {/* Toggle */}
          <div className="border-t border-border px-6 py-3.5 text-center text-[12.5px] text-muted">
            {mode === "login" ? (
              <>
                Need an account?{" "}
                <button
                  type="button"
                  onClick={() => { setMode("register"); setError(null); }}
                  className="font-semibold text-accentInk hover:underline"
                >
                  Register
                </button>
              </>
            ) : (
              <>
                Already have an account?{" "}
                <button
                  type="button"
                  onClick={() => { setMode("login"); setError(null); }}
                  className="font-semibold text-accentInk hover:underline"
                >
                  Sign in
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
