"use client";

import { type ChangeEvent } from "react";
import { cn } from "@/lib/utils";

export interface SwitchProps {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  label?: string;
  disabled?: boolean;
  className?: string;
  id?: string;
}

export function Switch({
  checked, onCheckedChange, label, disabled, className, id,
}: SwitchProps) {
  const handleChange = (e: ChangeEvent<HTMLInputElement>) =>
    onCheckedChange(e.target.checked);
  return (
    <label
      htmlFor={id}
      className={cn(
        "inline-flex items-center gap-2 cursor-pointer text-xs",
        disabled && "opacity-50 cursor-not-allowed",
        className,
      )}
    >
      <span className="relative inline-flex h-5 w-9">
        <input
          id={id}
          type="checkbox"
          checked={checked}
          onChange={handleChange}
          disabled={disabled}
          className="sr-only peer"
        />
        <span className="absolute inset-0 rounded-full bg-border peer-checked:bg-accent transition-colors" />
        <span className="absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-bg transition-transform peer-checked:translate-x-4" />
      </span>
      {label && <span className="text-muted">{label}</span>}
    </label>
  );
}
