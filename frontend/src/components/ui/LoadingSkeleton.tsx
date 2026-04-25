"use client";

import { Skeleton } from "@/components/ui/skeleton";

interface LoadingSkeletonProps {
  variant: "list" | "card" | "inline";
  rows?: number;
}

const WIDTHS = ["w-[90%]", "w-[75%]", "w-[82%]", "w-[68%]"] as const;

export function LoadingSkeleton({
  variant,
  rows = 3,
}: LoadingSkeletonProps) {
  if (variant === "inline") {
    return (
      <div className="flex items-center gap-2" aria-busy="true" aria-live="polite">
        <Skeleton className="h-4 w-24" />
        <Skeleton className="h-4 w-16" />
      </div>
    );
  }

  if (variant === "card") {
    return (
      <div
        className="rounded-lg border border-navy-700 bg-navy-900 overflow-hidden"
        aria-busy="true"
        aria-live="polite"
      >
        <Skeleton className="h-10 rounded-none border-b border-border bg-muted/70" />
        <div className="space-y-3 p-4">
          {Array.from({ length: rows }).map((_, i) => (
            <Skeleton
              key={i}
              className={`h-4 ${WIDTHS[i % WIDTHS.length]}`}
            />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2 p-3" aria-busy="true" aria-live="polite">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="rounded-md border border-navy-800 bg-navy-900/70 p-3">
          <Skeleton className={`h-4 ${WIDTHS[i % WIDTHS.length]}`} />
          <Skeleton className={`mt-2 h-4 ${WIDTHS[(i + 1) % WIDTHS.length]}`} />
        </div>
      ))}
    </div>
  );
}
