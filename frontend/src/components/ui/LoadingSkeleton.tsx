"use client";

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
        <div className="h-4 w-24 rounded bg-navy-700/40 animate-pulse" />
        <div className="h-4 w-16 rounded bg-navy-700/40 animate-pulse" />
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
        <div className="h-10 border-b border-navy-700 bg-navy-800/70 animate-pulse" />
        <div className="space-y-3 p-4">
          {Array.from({ length: rows }).map((_, i) => (
            <div
              key={i}
              className={`h-4 rounded bg-navy-700/40 animate-pulse ${WIDTHS[i % WIDTHS.length]}`}
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
          <div
            className={`h-4 rounded bg-navy-700/40 animate-pulse ${WIDTHS[i % WIDTHS.length]}`}
          />
          <div
            className={`mt-2 h-4 rounded bg-navy-700/30 animate-pulse ${WIDTHS[(i + 1) % WIDTHS.length]}`}
          />
        </div>
      ))}
    </div>
  );
}
