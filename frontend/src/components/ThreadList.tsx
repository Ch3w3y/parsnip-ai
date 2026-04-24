"use client";

import {
  useThreadStore,
  selectCurrentThreadId,
  selectError,
  selectIsLoading,
  selectThreads,
} from "../stores/thread-store";
import { EmptyState } from "./ui/EmptyState";
import { ErrorBanner } from "./ui/ErrorBanner";
import { LoadingSkeleton } from "./ui/LoadingSkeleton";

export function ThreadList() {
  const threads = useThreadStore(selectThreads);
  const currentThreadId = useThreadStore(selectCurrentThreadId);
  const switchToThread = useThreadStore((s) => s.switchToThread);
  const switchToNewThread = useThreadStore((s) => s.switchToNewThread);
  const isLoading = useThreadStore(selectIsLoading);
  const error = useThreadStore(selectError);
  const loadThreads = useThreadStore((s) => s.loadThreads);

  const formatTime = (iso: string | null) => {
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
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center justify-between px-3 py-3 border-b border-navy-700">
        <span className="text-xs font-semibold text-parsnip-muted uppercase tracking-wider">
          Threads
        </span>
        <div className="flex items-center gap-1">
          <button
            onClick={loadThreads}
            className="text-parsnip-muted hover:text-parsnip-teal transition-colors p-1 rounded"
            title="Refresh"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 12a9 9 0 1 1-6.219-8.56" />
              <polyline points="21,3 21,9 15,9" />
            </svg>
          </button>
          <button
            onClick={switchToNewThread}
            className="text-parsnip-muted hover:text-parsnip-teal transition-colors p-1 rounded"
            title="New thread"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {error && (
          <div className="p-3">
            <ErrorBanner
              message={error}
              detail="/api/agent/threads"
              onRetry={loadThreads}
            />
          </div>
        )}

        {!error && isLoading && threads.length === 0 && (
          <LoadingSkeleton variant="list" rows={5} />
        )}

        {!error && !isLoading && threads.length === 0 && (
          <EmptyState
            title="No threads yet"
            description="Start a conversation to create your first thread."
            cta={{
              label: "Start a conversation",
              onClick: switchToNewThread,
            }}
          />
        )}

        {threads.map((t) => (
          <button
            key={t.id}
            onClick={() => switchToThread(t.id)}
            className={`w-full text-left px-3 py-2.5 border-b border-navy-800 hover:bg-navy-800 transition-colors ${
              t.id === currentThreadId
                ? "bg-navy-800 border-l-2 border-l-parsnip-teal"
                : ""
            }`}
          >
            <div className="text-sm text-parsnip-text truncate">
              {t.title || "Untitled"}
            </div>
            <div className="flex items-center gap-2 mt-1">
              <span className="text-[10px] text-parsnip-muted">
                {t.message_count} msgs
              </span>
              {t.created_at && (
                <span className="text-[10px] text-parsnip-muted">
                  {formatTime(t.created_at)}
                </span>
              )}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
