import {
  LayoutDashboard,
  Sparkles,
  Network,
  FileCode2,
  Activity,
  BarChart3,
  HeartPulse,
  Boxes,
  PlugZap,
  Camera,
  Settings,
} from "lucide-react";

/** Single source of truth for app navigation.
 *
 *  Consumed by:
 *    - Sidebar        (desktop, grouped)
 *    - MobileNav      (below `md`, grouped drawer)
 *    - CommandPalette (flat list + `g <key>` chords)
 *
 *  Task-first IA (the command-center redesign): groups map to what you DO —
 *  Explore the code, Monitor the running system, Operate it.
 */
export interface NavItem {
  href: string;
  label: string;
  Icon: typeof Activity;
  hint: string;
  shortcut: string;
  paletteLabel?: string;
  paletteHint?: string;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

/** Command Center sits ABOVE the groups as the always-on cockpit home. */
export const HOME_ITEM: NavItem = {
  href: "/",
  label: "Command Center",
  Icon: LayoutDashboard,
  hint: "system cockpit",
  shortcut: "g h",
  paletteHint: "live cockpit",
};

export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Explore",
    items: [
      { href: "/ask",   label: "Ask your code", Icon: Sparkles,  hint: "hybrid search", shortcut: "g a", paletteLabel: "Ask your code" },
      { href: "/graph", label: "Graph",         Icon: Network,   hint: "map + trace",   shortcut: "g g" },
      { href: "/read",  label: "Read",          Icon: FileCode2, hint: "code browser",  shortcut: "g r" },
    ],
  },
  {
    label: "Monitor",
    items: [
      { href: "/activity", label: "Live Activity", Icon: Activity,   hint: "agent feed",      shortcut: "g l", paletteHint: "agent tool-call feed" },
      { href: "/metrics",  label: "Metrics",       Icon: BarChart3,  hint: "latency + usage", shortcut: "g m" },
      { href: "/health",   label: "Health",        Icon: HeartPulse, hint: "components",      shortcut: "g t" },
    ],
  },
  {
    label: "Operate",
    items: [
      { href: "/repositories", label: "Repositories", Icon: Boxes,    hint: "manage + ingest",  shortcut: "g p", paletteLabel: "Repositories" },
      { href: "/agents",       label: "Agents",       Icon: PlugZap,  hint: "connect + tools",  shortcut: "g c" },
      { href: "/snapshots",    label: "Snapshots",    Icon: Camera,   hint: "capture + replay", shortcut: "g s" },
      { href: "/settings",     label: "Settings",     Icon: Settings, hint: "weights + server", shortcut: "g e" },
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
