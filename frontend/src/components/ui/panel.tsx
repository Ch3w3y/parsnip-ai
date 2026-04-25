"use client";

import type { ReactNode } from "react";

import { Button, type ButtonProps } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export function PanelHeader({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={cn("flex items-center justify-between border-b border-border px-3 py-3", className)}>
      {children}
    </div>
  );
}

export function PanelTitle({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span className={cn("text-xs font-semibold uppercase tracking-wider text-muted-foreground", className)}>
      {children}
    </span>
  );
}

export function PanelActions({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("flex items-center gap-1", className)}>{children}</div>;
}

export function PanelIconButton({
  label,
  className,
  children,
  ...props
}: Omit<ButtonProps, "variant" | "size"> & { label: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className={cn("text-muted-foreground hover:text-primary", className)}
          aria-label={label}
          {...props}
        >
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}
