import { type HTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeStyles = cva(
  "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium font-mono border",
  {
    variants: {
      variant: {
        ok:    "bg-ok/10    text-ok    border-ok/30",
        warn:  "bg-warn/10  text-warn  border-warn/30",
        bad:   "bg-bad/10   text-bad   border-bad/30",
        accent:"bg-accent/10 text-accent border-accent/30",
        muted: "bg-panel    text-muted border-border",
      },
    },
    defaultVariants: { variant: "muted" },
  },
);

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeStyles> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return (
    <span className={cn(badgeStyles({ variant }), className)} {...props} />
  );
}
