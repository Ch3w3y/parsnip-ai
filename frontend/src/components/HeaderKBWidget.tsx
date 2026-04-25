"use client";

import { useState, useEffect, useRef, useCallback } from "react";

interface KBSearchResult {
  id?: string;
  source: string;
  content: string;
  score?: number;
}

export function HeaderKBWidget() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<KBSearchResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [isEndpointAvailable, setIsEndpointAvailable] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isOpen, setIsOpen] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

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
        setError(`Search failed (${res.status})`);
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

  const handleFocus = () => {
    setIsOpen(true);
  };

  const handleBlur = (e: React.FocusEvent) => {
    // Close dropdown if focus leaves the widget container
    if (containerRef.current && !containerRef.current.contains(e.relatedTarget as Node)) {
      setIsOpen(false);
    }
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

  const hasActiveQuery = query.trim().length > 0;

  return (
    <div
      ref={containerRef}
      className="relative"
      onBlur={handleBlur}
    >
      {/* Search trigger: icon button that expands to input */}
      <div className="flex items-center">
        <div className={`
          flex items-center transition-all duration-200 rounded-lg border
          ${isOpen || hasActiveQuery
            ? "w-64 bg-navy-900 border-navy-600"
            : "w-8 bg-transparent border-transparent"
          }
          focus-within:border-parsnip-teal
        `}>
          <button
            onClick={() => {
              setIsOpen(true);
              inputRef.current?.focus();
            }}
            className={`flex items-center justify-center w-8 h-8 shrink-0 transition-colors duration-150 ${
              isOpen || hasActiveQuery
                ? "text-parsnip-teal"
                : "text-parsnip-muted hover:text-parsnip-text"
            }`}
            title="Search knowledge base"
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
              <circle cx="11" cy="11" r="8" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
          </button>

          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => handleInputChange(e.target.value)}
            onFocus={handleFocus}
            placeholder="Search KB..."
            className={`
              bg-transparent text-sm text-parsnip-text placeholder:text-parsnip-muted
              focus:outline-none py-1.5 pr-2 transition-all duration-200
              ${isOpen || hasActiveQuery ? "w-full opacity-100" : "w-0 opacity-0 overflow-hidden" }
            `}
          />
        </div>
      </div>

      {/* Dropdown results */}
      {isOpen && (
        <div className="absolute top-full mt-1 left-0 w-72 max-h-80 overflow-y-auto bg-navy-950 border border-navy-700 rounded-lg shadow-xl z-50">
          {isEndpointAvailable === false && (
            <div className="px-3 py-4 text-center">
              <p className="text-sm text-parsnip-muted">Search from chat</p>
              <p className="text-xs text-parsnip-muted/60 mt-1">
                Knowledge-base search is available through the assistant.
              </p>
            </div>
          )}

          {isEndpointAvailable !== false && error && (
            <div className="px-3 py-3">
              <p className="text-sm text-red-400">{error}</p>
            </div>
          )}

          {isEndpointAvailable !== false && isSearching && (
            <div className="px-3 py-3">
              <div className="flex items-center gap-2">
                <div className="w-3 h-3 border-2 border-parsnip-teal/30 border-t-parsnip-teal rounded-full animate-spin" />
                <span className="text-xs text-parsnip-muted">Searching...</span>
              </div>
            </div>
          )}

          {isEndpointAvailable !== false && !isSearching && !error && results.length > 0 && (
            <div>
              {results.map((r, i) => (
                <div
                  key={r.id ?? i}
                  className="px-3 py-2 border-b border-navy-800 last:border-b-0 hover:bg-navy-900 transition-colors cursor-pointer"
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-navy-700 text-parsnip-muted">
                      {r.source}
                    </span>
                    {r.score != null && (
                      <span className="text-[10px] text-parsnip-muted">
                        {(r.score * 100).toFixed(0)}%
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-slate-200 leading-snug line-clamp-3">
                    {highlightMatch(
                      r.content.length > 140 ? r.content.slice(0, 140) + "…" : r.content,
                      query
                    )}
                  </p>
                </div>
              ))}
            </div>
          )}

          {isEndpointAvailable !== false && !isSearching && !error && results.length === 0 && hasActiveQuery && (
            <div className="px-3 py-4 text-center">
              <p className="text-sm text-parsnip-muted">No results for &ldquo;{query.trim()}&rdquo;</p>
              <p className="text-xs text-parsnip-muted/60 mt-1">Try a broader search term.</p>
            </div>
          )}

          {isEndpointAvailable !== false && !isSearching && !error && results.length === 0 && !hasActiveQuery && (
            <div className="px-3 py-4 text-center">
              <p className="text-xs text-parsnip-muted">Type to search the knowledge base</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}