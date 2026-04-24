"use client";

import { useEffect } from "react";
import {
  useKBStore,
  selectStats,
  selectIngestionStatus,
  selectIsLoadingStats,
  selectIsLoadingIngestion,
  selectError,
} from "../stores/kb-store";
import { EmptyState } from "./ui/EmptyState";
import { ErrorBanner } from "./ui/ErrorBanner";
import { LoadingSkeleton } from "./ui/LoadingSkeleton";

function RefreshIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
      <polyline points="21,3 21,9 15,9" />
    </svg>
  );
}

function formatTime(iso: string | null): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    if (diffMins < 1) return "just now";
    if (diffMins < 60) return `${diffMins}m ago`;
    const diffHrs = Math.floor(diffMins / 60);
    if (diffHrs < 24) return `${diffHrs}h ago`;
    const diffDays = Math.floor(diffHrs / 24);
    if (diffDays < 7) return `${diffDays}d ago`;
    return d.toLocaleDateString();
  } catch {
    return "";
  }
}

function capitalize(s: string): string {
  if (!s) return s;
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function KBStatsPanel() {
  const stats = useKBStore(selectStats);
  const ingestionStatus = useKBStore(selectIngestionStatus);
  const isLoadingStats = useKBStore(selectIsLoadingStats);
  const isLoadingIngestion = useKBStore(selectIsLoadingIngestion);
  const error = useKBStore(selectError);
  const loadStats = useKBStore((s) => s.loadStats);
  const loadIngestionStatus = useKBStore((s) => s.loadIngestionStatus);

  useEffect(() => {
    loadStats();
    loadIngestionStatus();
  }, [loadStats, loadIngestionStatus]);

  const isLoading = isLoadingStats || isLoadingIngestion;
  const totalChunks = stats.reduce((sum, s) => sum + s.chunks, 0);
  const maxChunks = Math.max(...stats.map((s) => s.chunks), 1);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center justify-between px-3 py-3 border-b border-navy-700">
        <span className="text-xs font-semibold text-parsnip-muted uppercase tracking-wider">
          Knowledge Base
        </span>
        <button
          onClick={() => {
            loadStats();
            loadIngestionStatus();
          }}
          className="text-parsnip-muted hover:text-parsnip-teal transition-colors p-1 rounded"
          title="Refresh"
        >
          <RefreshIcon />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {error && (
          <div className="p-3">
            <ErrorBanner
              message={error}
              detail="/api/agent/stats · /api/agent/ingestion/status"
              onRetry={() => {
                loadStats();
                loadIngestionStatus();
              }}
            />
          </div>
        )}

        {!error && isLoading && stats.length === 0 && !ingestionStatus && (
          <LoadingSkeleton variant="list" rows={5} />
        )}

        {!error && !isLoading && stats.length === 0 && !ingestionStatus && (
          <EmptyState
            title="No knowledge base data"
            description="Ingestion stats will appear here once the knowledge base is populated."
          />
        )}

        {stats.length > 0 && (
          <>
            <div className="px-3 py-3 border-b border-navy-800">
              <div className="text-2xl font-mono text-parsnip-teal">
                {totalChunks.toLocaleString()}
              </div>
              <div className="text-[10px] text-parsnip-muted mt-0.5">
                total chunks
              </div>
            </div>

            <div className="border-b border-navy-800">
              {stats.map((s) => (
                <div
                  key={s.source}
                  className="px-3 py-2.5 border-b border-navy-800 hover:bg-navy-800 transition-colors"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-parsnip-text">
                      {capitalize(s.source)}
                    </span>
                    <span className="text-sm text-parsnip-teal font-mono">
                      {s.chunks.toLocaleString()}
                    </span>
                  </div>
                  {s.last_updated && (
                    <span className="text-[10px] text-parsnip-muted">
                      {formatTime(s.last_updated)}
                    </span>
                  )}
                  {s.chunks > 0 && (
                    <div className="mt-1.5 w-full bg-navy-700 h-1 rounded-full overflow-hidden">
                      <div
                        className="bg-parsnip-teal/30 h-1 rounded-full"
                        style={{
                          width: `${Math.max(
                            (s.chunks / maxChunks) * 100,
                            2
                          )}%`,
                        }}
                      />
                    </div>
                  )}
                </div>
              ))}
            </div>
          </>
        )}

        <div>
          <div className="px-3 py-3">
            <span className="text-xs font-semibold text-parsnip-muted uppercase tracking-wider">
              Ingestion
            </span>
          </div>

          {ingestionStatus && Object.keys(ingestionStatus).length > 0 ? (
            <div className="px-3 pb-3 space-y-1.5">
              {Object.entries(ingestionStatus).map(([key, value]) => (
                <div
                  key={key}
                  className="flex items-center justify-between text-xs"
                >
                  <span className="text-parsnip-muted capitalize">
                    {key.replace(/_/g, " ")}
                  </span>
                  <span className="text-parsnip-text font-mono">
                    {typeof value === "boolean"
                      ? value
                        ? "Yes"
                        : "No"
                      : String(value)}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState
              title="No ingestion data"
              description="Refresh to retry fetching the ingestion status."
              cta={{
                label: "Retry",
                onClick: loadIngestionStatus,
              }}
            />
          )}
        </div>
      </div>
    </div>
  );
}
