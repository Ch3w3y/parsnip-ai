"use client";

import { useState } from "react";
import type { ToolCall } from "../../stores/thread-store";

interface ToolCallBadgeProps {
  toolCall: ToolCall;
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function prettyArgs(args: string): string {
  try {
    const parsed = JSON.parse(args);
    return JSON.stringify(parsed, null, 2);
  } catch {
    return args;
  }
}

export function ToolCallBadge({ toolCall }: ToolCallBadgeProps) {
  const [expanded, setExpanded] = useState(false);

  const { name, args, status, startedAt, endedAt, output, error } = toolCall;
  const duration =
    status !== "running" && endedAt ? endedAt - startedAt : undefined;

  const statusClasses = {
    running:
      "border-parsnip-teal/50 bg-parsnip-teal/10 text-parsnip-teal",
    done: "border-green-600/40 bg-green-900/20 text-green-300",
    error: "border-red-600/50 bg-red-900/20 text-red-300",
  }[status];

  const dotClasses = {
    running: "bg-parsnip-teal animate-pulse",
    done: "bg-green-400",
    error: "bg-red-400",
  }[status];

  return (
    <div className="my-1.5">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className={`inline-flex items-center gap-2 rounded-md border px-2 py-1 text-[11px] font-mono transition-colors hover:brightness-110 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-parsnip-teal ${statusClasses}`}
        aria-expanded={expanded}
        aria-label={`Tool call: ${name}, status ${status}${duration ? `, ${formatDuration(duration)}` : ""}`}
      >
        <span
          className={`h-1.5 w-1.5 rounded-full ${dotClasses}`}
          aria-hidden="true"
        />
        <span className="font-medium">{name}</span>
        {status === "running" && (
          <span className="text-[10px] opacity-70">running…</span>
        )}
        {duration !== undefined && (
          <span className="text-[10px] opacity-70">
            {formatDuration(duration)}
          </span>
        )}
        <svg
          width="10"
          height="10"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          className={`transition-transform ${expanded ? "rotate-90" : ""}`}
          aria-hidden="true"
        >
          <polyline points="9 18 15 12 9 6" />
        </svg>
      </button>

      {expanded && (
        <div className="mt-1 rounded-md border border-navy-600 bg-navy-900/70 p-2 text-[11px]">
          <div className="mb-1 text-[10px] uppercase tracking-wide text-parsnip-muted">
            Arguments
          </div>
          <pre className="overflow-x-auto whitespace-pre-wrap break-words text-parsnip-text/90">
            {prettyArgs(args)}
          </pre>
          {output !== undefined && (
            <>
              <div className="mt-2 mb-1 text-[10px] uppercase tracking-wide text-parsnip-muted">
                Output
              </div>
              <pre className="overflow-x-auto whitespace-pre-wrap break-words text-parsnip-text/90">
                {output.length > 400 ? `${output.slice(0, 400)}…` : output}
              </pre>
            </>
          )}
          {error !== undefined && (
            <>
              <div className="mt-2 mb-1 text-[10px] uppercase tracking-wide text-red-400">
                Error
              </div>
              <pre className="overflow-x-auto whitespace-pre-wrap break-words text-red-200">
                {error}
              </pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}
