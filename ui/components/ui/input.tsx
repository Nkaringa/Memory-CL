import { type InputHTMLAttributes, forwardRef } from "react";
import { cn } from "@/lib/utils";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        "h-9 w-full rounded-md border border-border bg-panel px-3 text-sm",
        "placeholder:text-muted/70 focus-visible:outline-none focus-visible:border-accent",
        "transition-colors font-mono",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => (
  <textarea
    ref={ref}
    className={cn(
      "w-full rounded-md border border-border bg-panel px-3 py-2 text-sm",
      "placeholder:text-muted/70 focus-visible:outline-none focus-visible:border-accent",
      "transition-colors font-mono resize-y min-h-[5rem]",
      className,
    )}
    {...props}
  />
));
Textarea.displayName = "Textarea";
