"use client";

import { useEffect, useState, type KeyboardEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { searchQnames } from "@/lib/api";

export interface QnameInputProps {
  value: string;
  onChange: (v: string) => void;
  /** Repo to search within; suggestions are disabled until one is selected. */
  repoId: string;
  id?: string;
  placeholder?: string;
  required?: boolean;
}

/** Controlled input with a debounced qualified-name autocomplete dropdown.
 *
 *  Degrades gracefully: any fetch error (or no repo selected) simply means
 *  no suggestions — the field always keeps working as a plain text input.
 */
export function QnameInput({
  value, onChange, repoId, id, placeholder, required,
}: QnameInputProps) {
  const [debounced, setDebounced] = useState(value);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);

  // 250 ms debounce so rapid typing doesn't flood the API.
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), 250);
    return () => clearTimeout(timer);
  }, [value]);

  const enabled = debounced.trim().length >= 2 && repoId !== "";
  const { data, isError } = useQuery({
    queryKey: ["qnames", repoId, debounced],
    queryFn: () => searchQnames(repoId, debounced),
    enabled,
    staleTime: 30_000,
    retry: false,
  });

  const matches = enabled && !isError ? (data?.matches ?? []) : [];
  const showList = open && matches.length > 0;

  function select(qname: string) {
    onChange(qname);
    setOpen(false);
    setActive(-1);
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Escape") {
      setOpen(false);
      setActive(-1);
      return;
    }
    if (!showList) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(i + 1, matches.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      const hit = active >= 0 ? matches[active] : undefined;
      if (hit) {
        e.preventDefault();
        select(hit.qualified_name);
      }
    }
  }

  return (
    <div className="relative">
      <Input
        id={id}
        value={value}
        required={required}
        placeholder={placeholder}
        autoComplete="off"
        onChange={(e) => {
          onChange(e.target.value);
          setOpen(true);
          setActive(-1);
        }}
        onKeyDown={onKeyDown}
        onBlur={() => setOpen(false)}
        role="combobox"
        aria-expanded={showList}
        aria-autocomplete="list"
      />
      {showList && (
        <ul
          role="listbox"
          // preventDefault keeps focus on the input so onBlur doesn't
          // close the list before an item's onClick can fire.
          onMouseDown={(e) => e.preventDefault()}
          className={cn(
            "absolute z-20 mt-1 w-full max-h-64 overflow-y-auto",
            "rounded-md border border-border bg-panel shadow-lg",
          )}
        >
          {matches.map((m, i) => (
            <li key={`${m.qualified_name}-${i}`} role="option" aria-selected={i === active}>
              <button
                type="button"
                onClick={() => select(m.qualified_name)}
                onMouseEnter={() => setActive(i)}
                className={cn(
                  "w-full text-left px-3 py-1.5 text-sm font-mono",
                  "flex items-center gap-2 transition-colors",
                  i === active ? "bg-accent/10 text-accent" : "hover:bg-bg/40",
                )}
              >
                <span className="flex-1 truncate">{m.qualified_name}</span>
                <Badge variant="muted" className="shrink-0 text-[10px]">
                  {m.kind}
                </Badge>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
