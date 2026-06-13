"use client";

import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { listRepos } from "@/lib/api";
import { cn } from "@/lib/utils";

export interface RepoSelectProps {
  value: string;
  onChange: (v: string) => void;
  /** Prepend an "all repos" option (value = ""). */
  allowAll?: boolean;
  id?: string;
  className?: string;
}

/** Shared repos query — cached under the same key the Command Center uses. */
export function useReposList() {
  return useQuery({ queryKey: ["repos"], queryFn: listRepos, retry: 1 });
}

/**
 * Light-theme native <select> for picking a repo. Controlled value/onChange.
 * Auto-selects the first repo once data arrives (unless `allowAll`, in which
 * case "" / all-repos is a legitimate resting state and we don't force a pick).
 * Self-heals a stale value that isn't in the loaded list.
 */
export function RepoSelect({
  value,
  onChange,
  allowAll = false,
  id,
  className,
}: RepoSelectProps) {
  const { data, isLoading } = useReposList();
  const repos = data?.repos ?? [];

  useEffect(() => {
    if (allowAll) return; // "" is valid (all repos) — never force a pick
    const first = repos[0];
    if (!first) return;
    if (value === "" || !repos.some((r) => r.repo_id === value)) {
      onChange(first.repo_id);
    }
  }, [repos, value, onChange, allowAll]);

  return (
    <select
      id={id}
      value={value}
      disabled={isLoading}
      onChange={(e) => onChange(e.target.value)}
      className={cn(
        "h-9 rounded-lg border border-border2 bg-bg px-3 text-[13px] font-mono text-fg",
        "appearance-none transition-colors focus:border-accent focus:outline-none",
        "disabled:text-muted",
        className,
      )}
    >
      {isLoading && <option value="">loading repos…</option>}
      {allowAll && <option value="">all repos</option>}
      {repos.map((r) => (
        <option key={r.repo_id} value={r.repo_id}>
          {r.repo_id} ({r.units} units)
        </option>
      ))}
      {!isLoading && repos.length === 0 && !allowAll && (
        <option value="">no repositories</option>
      )}
    </select>
  );
}
