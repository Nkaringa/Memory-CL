"use client";

import { type ReactNode, useState } from "react";
import { cn } from "@/lib/utils";

interface TooltipProps {
  content: ReactNode;
  children: ReactNode;
  side?: "top" | "bottom" | "left" | "right";
  className?: string;
}

/** Minimal CSS-positioned tooltip — no portal, no arrow, no JS
 *  positioning library. Sufficient for our hover previews.
 */
export function Tooltip({
  content, children, side = "top", className,
}: TooltipProps) {
  const [open, setOpen] = useState(false);
  const side_cls = {
    top:    "bottom-full left-1/2 -translate-x-1/2 mb-2",
    bottom: "top-full    left-1/2 -translate-x-1/2 mt-2",
    left:   "right-full  top-1/2  -translate-y-1/2 mr-2",
    right:  "left-full   top-1/2  -translate-y-1/2 ml-2",
  }[side];
  return (
    <span
      className="relative inline-flex"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      {children}
      {open && (
        <span
          role="tooltip"
          className={cn(
            "absolute z-50 whitespace-nowrap rounded-md border border-border",
            "bg-bg/95 backdrop-blur-sm px-2.5 py-1.5 text-xs shadow-lg",
            "pointer-events-none",
            side_cls,
            className,
          )}
        >
          {content}
        </span>
      )}
    </span>
  );
}
