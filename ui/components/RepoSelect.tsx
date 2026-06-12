"use client";

import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { listRepos } from "@/lib/api";

export interface RepoSelectProps {
  value: string;
  onChange: (v: string) => void;
  id?: string;
}

/** Shared query hook — other components (e.g. ToolRunner) can call this
 *  to get the same cached repos list without re-fetching. */
export function useRepos() {
  return useQuery({ queryKey: ["repos"], queryFn: listRepos });
}

const selectClasses = cn(
  "h-9 w-full rounded-md border border-border bg-panel px-3 text-sm",
  "focus-visible:outline-none focus-visible:border-accent",
  "transition-colors font-mono",
  // native select: remove default OS appearance, keep the caret legible
  "appearance-none",
);

export function RepoSelect({ value, onChange, id }: RepoSelectProps) {
  const { data, isLoading, isError } = useRepos();
  const repos = data?.repos ?? [];

  // Auto-select the first repo once data arrives, if nothing is selected yet.
  useEffect(() => {
    const first = repos[0];
    if (first && value === "") {
      onChange(first.repo_id);
    }
  }, [repos, value, onChange]);

  if (isLoading) {
    return (
      <select id={id} disabled className={cn(selectClasses, "text-muted/70")}>
        <option>loading repos…</option>
      </select>
    );
  }

  if (isError || repos.length === 0) {
    return (
      <Input
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="repo id (e.g. my-repo)"
      />
    );
  }

  return (
    <select
      id={id}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={selectClasses}
    >
      {repos.map((r) => (
        <option key={r.repo_id} value={r.repo_id}>
          {r.repo_id} ({r.units} units)
        </option>
      ))}
    </select>
  );
}
