"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState, Suspense } from "react";
import { fetchMe } from "@/lib/auth";
import { acceptInvite } from "@/lib/orgs";

function AcceptInviteForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";

  const [checking, setChecking] = useState(true);
  const [authed, setAuthed] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) {
      setChecking(false);
      return;
    }
    fetchMe().then((me) => {
      if (me.authenticated) {
        setAuthed(true);
        // Authenticated user: accept immediately, then redirect
        acceptInvite({ token })
          .then(() => router.replace("/"))
          .catch((err) => {
            setError(err instanceof Error ? err.message : "Could not accept invitation");
            setChecking(false);
          });
      } else {
        setAuthed(false);
        setChecking(false);
      }
    });
  }, [token, router]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await acceptInvite({
        token,
        email: email.trim(),
        password,
        display_name: displayName.trim(),
      });
      router.replace("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not accept invitation");
    } finally {
      setSubmitting(false);
    }
  }

  if (!token) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg px-4">
        <div className="w-full max-w-[400px] rounded-xl border border-border bg-bg p-6 text-center shadow-sm">
          <div className="text-[14px] font-semibold">Invalid invitation link</div>
          <div className="mt-1.5 text-[12.5px] text-muted">
            No invitation token was found in this URL. Ask your admin to resend the invite.
          </div>
        </div>
      </div>
    );
  }

  if (checking || authed) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg">
        <span className="text-[13px] text-muted">
          {authed ? "Accepting invitation…" : "Loading…"}
        </span>
        {error && (
          <div className="ml-3 rounded-lg border border-[#f0c9c9] bg-[#fef2f2] px-3 py-2 text-[12.5px] text-bad">
            {error}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg px-4">
      <div className="w-full max-w-[400px]">
        {/* Brand */}
        <div className="mb-8 flex flex-col items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-accent to-emerald-400 shadow-sm" />
          <div className="text-center">
            <div className="text-[19px] font-semibold tracking-tight">Memory-CL</div>
            <div className="mt-0.5 text-[12.5px] text-muted">Accept invitation</div>
          </div>
        </div>

        {/* Card */}
        <div className="overflow-hidden rounded-xl border border-border bg-bg shadow-sm">
          <div className="border-b border-border px-6 py-4">
            <div className="text-[14px] font-semibold">Create your account</div>
            <div className="mt-0.5 text-[12.5px] text-muted">
              You&apos;ve been invited to join. Fill in your details to get started.
            </div>
          </div>

          <form onSubmit={handleSubmit} className="space-y-3 px-6 py-5">
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
                autoComplete="email"
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
                autoComplete="new-password"
                className="w-full rounded-lg border border-border bg-bg px-3 py-2 text-[13px] outline-none focus:border-accent"
              />
            </div>

            {error && (
              <div className="rounded-lg border border-[#f0c9c9] bg-[#fef2f2] px-3.5 py-2.5 text-[12.5px] text-bad">
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={submitting}
              className="mt-1 w-full rounded-lg bg-accent px-4 py-2.5 text-[13.5px] font-semibold text-white transition-colors hover:bg-accentInk disabled:opacity-60"
            >
              {submitting ? "Creating account…" : "Accept invitation"}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}

export default function AcceptInvitePage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-bg">
          <span className="text-[13px] text-muted">Loading…</span>
        </div>
      }
    >
      <AcceptInviteForm />
    </Suspense>
  );
}
