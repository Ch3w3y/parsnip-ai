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
import { PanelActions, PanelHeader, PanelIconButton, PanelTitle } from "./ui/panel";
import { cn } from "@/lib/utils";

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
      <PanelHeader>
        <PanelTitle>Threads</PanelTitle>
        <PanelActions>
          <PanelIconButton
            onClick={loadThreads}
            label="Refresh threads"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 12a9 9 0 1 1-6.219-8.56" />
              <polyline points="21,3 21,9 15,9" />
            </svg>
          </PanelIconButton>
          <PanelIconButton
            onClick={switchToNewThread}
            label="New thread"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
          </PanelIconButton>
        </PanelActions>
      </PanelHeader>

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
            className={cn(
              "w-full border-b border-border px-3 py-2.5 text-left transition-colors hover:bg-accent",
              t.id === currentThreadId
                ? "border-l-2 border-l-primary bg-accent"
                : "",
            )}
          >
            <div className="truncate text-sm text-foreground">
              {t.title || "Untitled"}
            </div>
            <div className="flex items-center gap-2 mt-1">
              <span className="text-[10px] text-muted-foreground">
                {t.message_count} msgs
              </span>
              {t.created_at && (
                <span className="text-[10px] text-muted-foreground">
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
