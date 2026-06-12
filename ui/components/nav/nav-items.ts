import {
  Activity,
  Boxes,
  Database,
  GitGraph,
  ScrollText,
  ShieldCheck,
  Sparkles,
  Terminal,
  Workflow,
} from "lucide-react";

/** Single source of truth for app navigation.
 *
 *  Consumed by:
 *    - Sidebar        (desktop, grouped)
 *    - MobileNav      (below `md`, grouped drawer)
 *    - CommandPalette (flat list + `g <key>` chords)
 */
export interface NavItem {
  href: string;
  label: string;
  Icon: typeof Activity;
  hint: string;
  /** `g <key>` chord used by the command palette. */
  shortcut: string;
  /** Palette-specific copy overrides (the palette predates the grouped sidebar). */
  paletteLabel?: string;
  paletteHint?: string;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

/** Dashboard sits ABOVE the groups as the always-on home; it's not a
 *  "Core" page — it's the launching pad.
 */
export const HOME_ITEM: NavItem = {
  href: "/dashboard",
  label: "Dashboard",
  Icon: Activity,
  hint: "system pulse",
  shortcut: "g d",
};

/** Information architecture per Phase-10 polish spec.
 *
 *    Core      — what an agent / engineer reaches for first
 *    System    — operational + audit surfaces
 *    Dev Tools — registry + ad-hoc tool runner
 */
export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Core",
    items: [
      { href: "/retrieve",  label: "Retrieve",  Icon: Sparkles,  hint: "hybrid + ranked", shortcut: "g r", paletteHint: "primary surface" },
      { href: "/graph",     label: "Graph",     Icon: GitGraph,  hint: "BFS explorer",    shortcut: "g g" },
      { href: "/ingest",    label: "Ingest",    Icon: Boxes,     hint: "repo intake",     shortcut: "g i" },
    ],
  },
  {
    label: "System",
    items: [
      { href: "/status",    label: "Status",    Icon: ShieldCheck, hint: "boot + flags",  shortcut: "g t" },
      { href: "/audit",     label: "Audit",     Icon: ScrollText,  hint: "hash chain",    shortcut: "g a" },
      { href: "/snapshot",  label: "Snapshot",  Icon: Database,    hint: "replay engine", shortcut: "g s", paletteHint: "deterministic state" },
    ],
  },
  {
    label: "Dev Tools",
    items: [
      { href: "/mcp",         label: "MCP",         Icon: Workflow, hint: "tool registry", shortcut: "g m", paletteLabel: "MCP Tools" },
      { href: "/tool-runner", label: "Tool Runner", Icon: Terminal, hint: "ad-hoc invoke", shortcut: "g k" },
    ],
  },
];

/** Flat list (home first) for the command palette and chord map. */
export const ALL_NAV_ITEMS: NavItem[] = [
  HOME_ITEM,
  ...NAV_GROUPS.flatMap((group) => group.items),
];

export function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(href + "/");
}
