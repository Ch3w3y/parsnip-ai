"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { useKBStore } from "../stores/kb-store";
import { EmptyState } from "./ui/EmptyState";
import { ErrorBanner } from "./ui/ErrorBanner";
import { LoadingSkeleton } from "./ui/LoadingSkeleton";

interface KBSearchResult {
  id?: string;
  source: string;
  content: string;
  score?: number;
}

export function KBSearchPanel() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<KBSearchResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [isEndpointAvailable, setIsEndpointAvailable] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const stats = useKBStore((s) => s.stats);
  const loadStats = useKBStore((s) => s.loadStats);

  useEffect(() => {
    loadStats();
  }, [loadStats]);

  const totalSources = stats.length;
  const totalChunks = stats.reduce((acc, s) => acc + s.chunks, 0);

  const doSearch = useCallback(async (q: string) => {
    if (!q.trim()) {
      setResults([]);
      setIsSearching(false);
      setError(null);
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setIsSearching(true);
    setError(null);

    try {
      const res = await fetch(
        `/api/agent/kb-search?q=${encodeURIComponent(q.trim())}&limit=5`,
        { signal: controller.signal }
      );

      if (res.status === 404) {
        setIsEndpointAvailable(false);
        setResults([]);
        setIsSearching(false);
        return;
      }

      if (!res.ok) {
        setResults([]);
        setError(`Failed to search knowledge base (${res.status})`);
        setIsSearching(false);
        return;
      }

      const data = await res.json();
      setIsEndpointAvailable(true);

      const items = Array.isArray(data)
        ? data
        : Array.isArray(data.results)
          ? data.results
          : [];

      setResults(
        items.map((item: Record<string, unknown>) => ({
          id: (item.id as string) ?? undefined,
          source: (item.source as string) ?? (item.metadata as Record<string, unknown>)?.source as string ?? "unknown",
          content: (item.content as string) ?? (item.text as string) ?? "",
          score: (item.score as number) ?? undefined,
        }))
      );
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setResults([]);
      setError("Unable to search knowledge base");
    } finally {
      if (controller === abortRef.current) {
        setIsSearching(false);
      }
    }
  }, []);

  const handleInputChange = (value: string) => {
    setQuery(value);

    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      doSearch(value);
    }, 300);
  };

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      abortRef.current?.abort();
    };
  }, []);

  const highlightMatch = (text: string, q: string) => {
    if (!q.trim()) return text;
    const escaped = q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const parts = text.split(new RegExp(`(${escaped})`, "gi"));
    return parts.map((part, i) =>
      part.toLowerCase() === q.toLowerCase() ? (
        <span key={i} className="text-parsnip-teal font-medium">
          {part}
        </span>
      ) : (
        part
      )
    );
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center justify-between px-3 py-3 border-b border-navy-700">
        <span className="text-xs font-semibold text-parsnip-muted uppercase tracking-wider">
          Knowledge Base
        </span>
        <button
          onClick={loadStats}
          className="text-parsnip-muted hover:text-parsnip-teal transition-colors p-1 rounded"
          title="Refresh stats"
        >
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
        </button>
      </div>

      <div className="px-3 py-2 border-b border-navy-700">
        <div className="relative">
          <svg
            className="absolute left-2.5 top-1/2 -translate-y-1/2 text-parsnip-muted"
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input
            type="text"
            value={query}
            onChange={(e) => handleInputChange(e.target.value)}
            placeholder="Search knowledge base..."
            className="w-full bg-navy-800 border border-navy-700 rounded-lg pl-8 pr-3 py-2 text-sm text-parsnip-text placeholder:text-parsnip-muted focus:outline-none focus:border-parsnip-teal transition-colors"
          />
        </div>
      </div>


      {isEndpointAvailable === false && (
        <EmptyState
          icon={
            <svg
              width="28"
              height="28"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="16" x2="12" y2="12" />
              <line x1="12" y1="8" x2="12.01" y2="8" />
            </svg>
          }
          title="Search from chat"
          description="Knowledge-base search is currently exposed through the assistant chat."
        />
      )}


      {isEndpointAvailable !== false && (
        <div className="flex-1 overflow-y-auto">
          {error && (
            <div className="p-3">
              <ErrorBanner
                message={error}
                detail="/api/agent/kb-search"
                onRetry={() => void doSearch(query)}
              />
            </div>
          )}

          {!error && isSearching && (
            <LoadingSkeleton variant="list" rows={3} />
          )}


          {!isSearching && !error && results.length > 0 && (
            <div>
              {results.map((r, i) => (
                <div
                  key={r.id ?? i}
                  className="px-3 py-2.5 border-b border-navy-800 last:border-b-0"
                >
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-navy-700 text-parsnip-muted">
                      {r.source}
                    </span>
                    {r.score != null && (
                      <span className="text-[10px] text-parsnip-muted">
                        {(r.score * 100).toFixed(0)}%
                      </span>
                    )}
                  </div>
                  <p className="text-sm text-parsnip-text leading-snug line-clamp-4">
                    {highlightMatch(r.content, query)}
                  </p>
                </div>
              ))}
            </div>
          )}


          {!isSearching && !error && results.length === 0 && !query.trim() && (
            <EmptyState
              title="Search the knowledge base"
              description={`${totalSources} source${totalSources !== 1 ? "s" : ""} · ${totalChunks.toLocaleString()} chunk${totalChunks !== 1 ? "s" : ""}`}
            />
          )}


          {!isSearching && !error && results.length === 0 && query.trim() && (
            <EmptyState
              title={`No results for “${query.trim()}”`}
              description="Try a broader search term."
            />
          )}
        </div>
      )}
    </div>
  );
}
