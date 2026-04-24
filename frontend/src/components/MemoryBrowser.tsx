"use client";

import { useEffect, useRef, useState } from "react";
import {
  useMemoryStore,
  selectMemories,
  selectDeletingIds,
  selectDeleteErrors,
  selectIsLoading,
  selectError,
  selectCategoryFilter,
  selectImportanceFilter,
  selectSearchQuery,
  MEMORY_CATEGORIES,
  IMPORTANCE_RANGE,
} from "../stores/memory-store";
import { EmptyState } from "./ui/EmptyState";
import { ErrorBanner } from "./ui/ErrorBanner";
import { LoadingSkeleton } from "./ui/LoadingSkeleton";

function formatTime(iso: string | null) {
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

export function MemoryBrowser() {
  const memories = useMemoryStore(selectMemories);
  const deletingIds = useMemoryStore(selectDeletingIds);
  const deleteErrors = useMemoryStore(selectDeleteErrors);
  const isLoading = useMemoryStore(selectIsLoading);
  const error = useMemoryStore(selectError);
  const categoryFilter = useMemoryStore(selectCategoryFilter);
  const importanceFilter = useMemoryStore(selectImportanceFilter);
  const searchQuery = useMemoryStore(selectSearchQuery);

  const loadMemories = useMemoryStore((s) => s.loadMemories);
  const deleteMemory = useMemoryStore((s) => s.deleteMemory);
  const setCategoryFilter = useMemoryStore((s) => s.setCategoryFilter);
  const setImportanceFilter = useMemoryStore((s) => s.setImportanceFilter);
  const setSearchQuery = useMemoryStore((s) => s.setSearchQuery);
  const clearFilters = useMemoryStore((s) => s.clearFilters);

  const [filtersOpen, setFiltersOpen] = useState(false);
  const didMountSearch = useRef(false);

  useEffect(() => {
    loadMemories();
  }, [loadMemories, categoryFilter, importanceFilter]);

  useEffect(() => {
    if (!didMountSearch.current) {
      didMountSearch.current = true;
      return;
    }
    const timeout = setTimeout(() => {
      void useMemoryStore.getState().loadMemories();
    }, 300);
    return () => clearTimeout(timeout);
  }, [searchQuery]);

  const handleDelete = (id: number) => {
    if (window.confirm("Delete this memory?")) {
      deleteMemory(id);
    }
  };

  const hasActiveFilters = categoryFilter || importanceFilter > 0 || searchQuery;

  return (
    <div className="flex flex-col h-full overflow-hidden">
<div className="flex items-center justify-between px-3 py-3 border-b border-navy-700">
        <span className="text-xs font-semibold text-parsnip-muted uppercase tracking-wider">
          Memories
        </span>
        <div className="flex items-center gap-1">
          <button
            onClick={loadMemories}
            className="text-parsnip-muted hover:text-parsnip-teal transition-colors p-1 rounded"
            title="Refresh"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 12a9 9 0 1 1-6.219-8.56" />
              <polyline points="21,3 21,9 15,9" />
            </svg>
          </button>
          <button
            onClick={() => setFiltersOpen(!filtersOpen)}
            className={`transition-colors p-1 rounded ${
              filtersOpen
                ? "text-parsnip-teal"
                : "text-parsnip-muted hover:text-parsnip-teal"
            }`}
            title="Toggle filters"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polygon points="22,3 2,3 10,12.46 10,19 14,21 14,12.46" />
            </svg>
          </button>
        </div>
      </div>


      {filtersOpen && (
        <div className="border-b border-navy-700">

          <div className="px-3 pt-2 pb-1">
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search memories..."
              className="w-full bg-navy-800 border border-navy-700 rounded text-sm text-parsnip-text placeholder-parsnip-muted px-2 py-1.5 focus:outline-none focus:border-parsnip-teal transition-colors"
            />
          </div>

<div className="flex items-center gap-1.5 px-3 py-2 overflow-x-auto">
            <button
              onClick={() => setCategoryFilter("")}
              className={`shrink-0 text-xs px-2 py-1 rounded border transition-colors ${
                !categoryFilter
                  ? "bg-parsnip-teal/20 text-parsnip-teal border-parsnip-teal"
                  : "bg-navy-800 text-parsnip-muted border-navy-700 hover:text-parsnip-text"
              }`}
            >
              All
            </button>
            {MEMORY_CATEGORIES.map((cat) => (
              <button
                key={cat}
                onClick={() => setCategoryFilter(cat === categoryFilter ? "" : cat)}
                className={`shrink-0 text-xs px-2 py-1 rounded border transition-colors ${
                  categoryFilter === cat
                    ? "bg-parsnip-teal/20 text-parsnip-teal border-parsnip-teal"
                    : "bg-navy-800 text-parsnip-muted border-navy-700 hover:text-parsnip-text"
                }`}
              >
                {cat.replace("_", " ")}
              </button>
            ))}
          </div>

<div className="flex items-center gap-2 px-3 pb-2">
            <span className="text-[10px] text-parsnip-muted shrink-0">Importance:</span>
            <div className="flex items-center gap-1">
              {Array.from({ length: IMPORTANCE_RANGE.max }, (_, i) => i + 1).map((level) => (
                <button
                  key={level}
                  onClick={() => setImportanceFilter(importanceFilter === level ? 0 : level)}
                  className={`flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] border transition-colors ${
                    importanceFilter === level
                      ? "bg-parsnip-teal/20 text-parsnip-teal border-parsnip-teal"
                      : "bg-navy-800 text-parsnip-muted border-navy-700 hover:text-parsnip-text"
                  }`}
                  title={`Min importance: ${IMPORTANCE_RANGE.labels[level - 1]}`}
                >
                  {level}
                  <span className="inline-block w-1 h-1 rounded-full bg-current" />
                </button>
              ))}
            </div>
          </div>


          {hasActiveFilters && (
            <div className="px-3 pb-2">
              <button
                onClick={clearFilters}
                className="text-[10px] text-parsnip-muted hover:text-parsnip-teal transition-colors"
              >
                Clear all filters
              </button>
            </div>
          )}
        </div>
      )}


      {error && (
        <div className="border-b border-navy-700 p-3">
          <ErrorBanner
            message={error}
            detail="/api/agent/memories"
            onRetry={loadMemories}
          />
        </div>
      )}


      <div className="flex-1 overflow-y-auto">
        {!error && isLoading && memories.length === 0 && (
          <LoadingSkeleton variant="list" rows={5} />
        )}

        {!error && !isLoading && memories.length === 0 && (
          <EmptyState
            title="No memories found"
            description={
              hasActiveFilters
                ? "Try clearing or adjusting your filters."
                : "Saved memories will appear here after the agent stores them."
            }
            cta={
              hasActiveFilters
                ? {
                    label: "Clear filters",
                    onClick: () => {
                      clearFilters();
                      loadMemories();
                    },
                  }
                : undefined
            }
          />
        )}

        {memories.map((m) => (
          <div
            key={m.id}
            className="px-3 py-2.5 border-b border-navy-800 hover:bg-navy-800 transition-colors"
          >
<p className="text-sm text-parsnip-text line-clamp-3 leading-snug">
              {m.content}
            </p>


            <div className="flex items-center gap-2 mt-1.5">
<span className="text-[10px] px-1.5 py-0.5 rounded bg-navy-700 text-parsnip-muted">
                {m.category.replace("_", " ")}
              </span>

<div className="flex items-center gap-0.5" title={`Importance: ${IMPORTANCE_RANGE.labels[m.importance - 1]}`}>
                {Array.from({ length: IMPORTANCE_RANGE.max }, (_, i) => i + 1).map((pip) => (
                  <span
                    key={pip}
                    className={`inline-block w-1.5 h-1.5 rounded-full ${
                      pip <= m.importance ? "bg-parsnip-teal" : "bg-navy-700"
                    }`}
                  />
                ))}
              </div>

              {(m.created_at || m.updated_at) && (
                <span className="text-[10px] text-parsnip-muted">
                  {formatTime(m.updated_at || m.created_at)}
                </span>
              )}

              <button
                onClick={() => handleDelete(m.id)}
                disabled={deletingIds.has(m.id)}
                className="ml-auto text-parsnip-muted hover:text-red-400 transition-colors p-0.5 rounded"
                title="Delete memory"
              >
                {deletingIds.has(m.id) ? (
                  <span
                    className="inline-block h-2.5 w-2.5 rounded-full border border-current border-t-transparent animate-spin"
                    aria-hidden="true"
                  />
                ) : (
                  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <line x1="18" y1="6" x2="6" y2="18" />
                    <line x1="6" y1="6" x2="18" y2="18" />
                  </svg>
                )}
              </button>
            </div>
            {deleteErrors[m.id] ? (
              <div className="mt-1 text-[10px] text-red-300">{deleteErrors[m.id]}</div>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}
