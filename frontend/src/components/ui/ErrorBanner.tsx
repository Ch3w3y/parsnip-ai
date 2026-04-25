"use client";

import { Button } from "@/components/ui/button";

interface ErrorBannerProps {
  message: string;
  onRetry?: () => void;
  detail?: string;
}

export function ErrorBanner({ message, onRetry, detail }: ErrorBannerProps) {
  return (
    <div
      role="alert"
      aria-live="assertive"
      className="flex items-start gap-3 rounded-md border border-destructive/40 bg-destructive/15 p-3 text-destructive"
    >
      <svg
        className="mt-0.5 h-4 w-4 shrink-0"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <circle cx="12" cy="12" r="10" />
        <line x1="12" y1="8" x2="12" y2="12" />
        <line x1="12" y1="16" x2="12.01" y2="16" />
      </svg>

      <div className="min-w-0 flex-1">
        <div className="text-sm">{message}</div>
        {detail ? (
          <div className="mt-1 truncate text-xs text-red-200/75">{detail}</div>
        ) : null}
      </div>

      {onRetry ? (
        <Button
          type="button"
          onClick={onRetry}
          variant="destructive"
          size="xs"
          className="shrink-0"
        >
          Retry
        </Button>
      ) : null}
    </div>
  );
}
