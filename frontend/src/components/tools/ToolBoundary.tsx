"use client";

import { ErrorBoundary } from "react-error-boundary";

function ToolErrorFallback({ error }: { error: Error }) {
  return (
    <div className="tool-card border-parsnip-error/40">
      <span className="text-parsnip-error text-xs">⚠ Tool UI error: {error.message}</span>
    </div>
  );
}

export function ToolBoundary({ children }: { children: React.ReactNode }) {
  return (
    <ErrorBoundary FallbackComponent={ToolErrorFallback}>
      {children}
    </ErrorBoundary>
  );
}