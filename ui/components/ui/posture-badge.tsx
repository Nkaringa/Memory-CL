import { ShieldAlert, ShieldCheck, ShieldX, ShieldHalf } from "lucide-react";
import type { StatusResponse } from "@/lib/types";
import { cn } from "@/lib/utils";

export type Posture = "OK" | "DEGRADED" | "SAFE_MODE" | "FAILED" | "UNKNOWN";

/** Single source of truth for translating /status into an at-a-glance
 *  posture. Used by the dashboard pill, the status page header, and
 *  the sidebar status indicator.
 *
 *  Order of precedence is intentional:
 *      FAILED  > SAFE_MODE > DEGRADED > OK
 *  so the UI never under-reports a serious condition.
 */
export function computePosture(status: StatusResponse | null | undefined): Posture {
  if (!status) return "UNKNOWN";
  if (!status.boot_overall_ok || status.boot_failed_stages.length > 0) {
    return "FAILED";
  }
  if (status.safe_mode.enabled) return "SAFE_MODE";
  if (status.boot_degraded_stages.length > 0) return "DEGRADED";
  return "OK";
}

const VARIANT: Record<Posture, {
  label: string;
  Icon: typeof ShieldCheck;
  cls: string;
  pulse: boolean;
}> = {
  OK:        { label: "Operational",  Icon: ShieldCheck, cls: "bg-ok/10  text-ok  border-ok/30",   pulse: false },
  DEGRADED:  { label: "Degraded",     Icon: ShieldHalf,  cls: "bg-warn/10 text-warn border-warn/30", pulse: true  },
  SAFE_MODE: { label: "Safe mode",    Icon: ShieldAlert, cls: "bg-warn/10 text-warn border-warn/30", pulse: true  },
  FAILED:    { label: "Failed",       Icon: ShieldX,     cls: "bg-bad/10  text-bad  border-bad/30",  pulse: true  },
  UNKNOWN:   { label: "Unknown",      Icon: ShieldHalf,  cls: "bg-panel  text-muted border-border",   pulse: false },
};

interface PostureBadgeProps {
  posture: Posture;
  size?: "sm" | "md";
  showIcon?: boolean;
  className?: string;
}

export function PostureBadge({
  posture, size = "md", showIcon = true, className,
}: PostureBadgeProps) {
  const v = VARIANT[posture];
  const heightCls = size === "sm" ? "h-5 px-2 text-[10px]" : "h-6 px-2.5 text-xs";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border font-mono uppercase tracking-wider",
        heightCls,
        v.cls,
        className,
      )}
      aria-label={`system posture: ${v.label}`}
    >
      {showIcon && (
        <v.Icon
          size={size === "sm" ? 10 : 12}
          className={cn(v.pulse && "animate-pulse")}
        />
      )}
      <span>{v.label}</span>
    </span>
  );
}
