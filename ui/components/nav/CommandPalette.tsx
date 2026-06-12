"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Command } from "lucide-react";
import { cn } from "@/lib/utils";
import { ALL_NAV_ITEMS } from "@/components/nav/nav-items";

interface PaletteItem {
  href: string;
  label: string;
  hint: string;
  shortcut: string;
}

// Single nav registry (components/nav/nav-items.ts) drives the palette;
// palette-specific copy comes through the paletteLabel/paletteHint overrides.
const ITEMS: PaletteItem[] = ALL_NAV_ITEMS.map((item) => ({
  href: item.href,
  label: item.paletteLabel ?? item.label,
  hint: item.paletteHint ?? item.hint,
  shortcut: item.shortcut,
}));

// `g d` → { d: "/dashboard" }, etc.
const CHORD_MAP: Record<string, string> = Object.fromEntries(
  ALL_NAV_ITEMS.map((item) => [item.shortcut.split(" ")[1], item.href]),
);

/** Ctrl/⌘-K palette + simple `g <key>` shortcuts. */
export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const router = useRouter();

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
      } else if (e.key === "Escape") {
        setOpen(false);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // `g r` → /retrieve, etc.
  useEffect(() => {
    let pending = false;
    function onKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null;
      if (target && /input|textarea|select/i.test(target.tagName)) return;
      if (e.key === "g" && !pending) {
        pending = true;
        setTimeout(() => { pending = false; }, 800);
        return;
      }
      if (pending) {
        const dest = CHORD_MAP[e.key.toLowerCase()];
        if (dest) {
          pending = false;
          router.push(dest as never);
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router]);

  if (!open) return null;
  const filtered = ITEMS.filter(
    (i) =>
      !query ||
      i.label.toLowerCase().includes(query.toLowerCase()) ||
      i.hint.toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-bg/70 backdrop-blur-sm pt-32"
      onClick={() => setOpen(false)}
    >
      <div
        className="w-full max-w-md rounded-lg border border-border bg-panel shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-border px-3">
          <Command size={14} className="text-muted" />
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search pages…"
            className="h-10 flex-1 bg-transparent text-sm focus-visible:outline-none font-mono"
          />
          <kbd className="text-[10px] text-muted font-mono">esc</kbd>
        </div>
        <div className="max-h-80 overflow-auto p-2">
          {filtered.length === 0 ? (
            <div className="text-xs text-muted px-3 py-6 text-center">no matches</div>
          ) : (
            filtered.map((item) => (
              <button
                key={item.href}
                type="button"
                className={cn(
                  "flex w-full items-center justify-between rounded px-3 py-2 text-left",
                  "text-sm hover:bg-bg/60",
                )}
                onClick={() => {
                  setOpen(false);
                  router.push(item.href as never);
                }}
              >
                <div>
                  <div>{item.label}</div>
                  <div className="text-[10px] muted">{item.hint}</div>
                </div>
                <kbd className="text-[10px] muted font-mono">{item.shortcut}</kbd>
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
