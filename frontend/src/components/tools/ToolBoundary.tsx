"use client";

import { ErrorBoundary } from "react-error-boundary";
import { Card } from "@/components/ui/card";

function ToolErrorFallback({ error }: { error: Error }) {
  return (
    <Card className="my-2 border-parsnip-error/40 bg-card p-4">
      <span className="text-parsnip-error text-xs">⚠ Tool UI error: {error.message}</span>
    </Card>
  );
}

export function ToolBoundary({ children }: { children: React.ReactNode }) {
  return (
    <ErrorBoundary FallbackComponent={ToolErrorFallback}>
      {children}
    </ErrorBoundary>
  );
}
