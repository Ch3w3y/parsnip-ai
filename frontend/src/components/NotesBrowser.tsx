"use client";

import { useEffect, useRef, useState } from "react";
import {
  useNoteStore,
  selectNotes,
  selectIsLoading,
  selectError,
  selectNotebooks,
  selectNotebookFilter,
  selectSearchQuery,
} from "../stores/note-store";
import type { NoteSummary } from "../stores/note-store";
import { usePanelStore } from "../stores/panel-store";
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

export function NotesBrowser() {
  const notes = useNoteStore(selectNotes);
  const isLoading = useNoteStore(selectIsLoading);
  const error = useNoteStore(selectError);
  const notebooks = useNoteStore(selectNotebooks);
  const notebookFilter = useNoteStore(selectNotebookFilter);
  const searchQuery = useNoteStore(selectSearchQuery);

  const loadNotes = useNoteStore((s) => s.loadNotes);
  const loadNotebooks = useNoteStore((s) => s.loadNotebooks);
  const setCurrentNoteId = useNoteStore((s) => s.setCurrentNoteId);
  const createNote = useNoteStore((s) => s.createNote);
  const deleteNote = useNoteStore((s) => s.deleteNote);
  const setSearchQuery = useNoteStore((s) => s.setSearchQuery);
  const setNotebookFilter = useNoteStore((s) => s.setNotebookFilter);
  const clearFilters = useNoteStore((s) => s.clearFilters);
  const setRightPanel = usePanelStore((s) => s.setRightPanel);

  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const didMountSearch = useRef(false);
  useEffect(() => {
    loadNotebooks();
  }, [loadNotebooks]);

  useEffect(() => {
    loadNotes(notebookFilter || undefined, searchQuery || undefined);
  }, [loadNotes, notebookFilter]);

  useEffect(() => {
    if (!didMountSearch.current) {
      didMountSearch.current = true;
      return;
    }
    const timeout = setTimeout(() => {
      const { notebookFilter: currentNotebookFilter, searchQuery: currentSearch } =
        useNoteStore.getState();
      loadNotes(
        currentNotebookFilter || undefined,
        currentSearch || undefined,
      );
    }, 300);
    return () => clearTimeout(timeout);
  }, [searchQuery, loadNotes]);

  const handleNewNote = async () => {
    if (isCreating) return;
    setIsCreating(true);
    try {
      await createNote("Untitled Note", "", notebookFilter || undefined, []);
    } finally {
      setIsCreating(false);
    }
  };

  const handleDelete = (noteId: string) => {
    if (window.confirm("Delete this note?")) {
      deleteNote(noteId);
    }
  };

  const handleSelectNote = (noteId: string) => {
    setCurrentNoteId(noteId);
    setRightPanel("note");
  };

  const hasActiveFilters = notebookFilter || searchQuery;

  return (
    <div className="flex flex-col h-full overflow-hidden bg-navy-900">
      <div className="flex items-center justify-between px-3 py-3 border-b border-navy-700">
        <span className="text-xs font-semibold text-parsnip-muted uppercase tracking-wider">
          Notes
        </span>
        <div className="flex items-center gap-1">
          <button
            onClick={handleNewNote}
            disabled={isCreating}
            className="text-parsnip-teal hover:text-parsnip-teal/80 transition-colors p-1 rounded disabled:opacity-50"
            title="New note"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
          </button>
          <button
            onClick={() => {
              loadNotes(notebookFilter || undefined, searchQuery || undefined);
              loadNotebooks();
            }}
            className="text-parsnip-muted hover:text-parsnip-teal transition-colors p-1 rounded"
            title="Refresh"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 12a9 9 0 1 1-6.219-8.56" />
              <polyline points="21,3 21,9 15,9" />
            </svg>
          </button>
        </div>
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
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search notes..."
            className="w-full bg-navy-800 border border-navy-700 rounded pl-8 pr-3 py-1.5 text-sm text-parsnip-text placeholder:text-parsnip-muted focus:outline-none focus:border-parsnip-teal transition-colors"
          />
        </div>
      </div>

      {notebooks.length > 0 && (
        <div className="px-3 py-2 border-b border-navy-700">
          <select
            value={notebookFilter}
            onChange={(e) => setNotebookFilter(e.target.value)}
            className="w-full bg-navy-800 border border-navy-700 rounded text-sm text-parsnip-text px-2 py-1.5 focus:outline-none focus:border-parsnip-teal transition-colors"
          >
            <option value="">All notebooks</option>
            {notebooks.map((nb) => (
              <option key={nb.id} value={nb.id}>
                {nb.title} ({nb.note_count})
              </option>
            ))}
          </select>
        </div>
      )}

      {hasActiveFilters && (
        <div className="px-3 py-1.5 border-b border-navy-700">
          <button
            onClick={() => {
              clearFilters();
              loadNotes(undefined, undefined);
            }}
            className="text-[10px] text-parsnip-muted hover:text-parsnip-teal transition-colors"
          >
            Clear filters
          </button>
        </div>
      )}

      {error && (
        <div className="border-b border-navy-700 p-3">
          <ErrorBanner
            message={error}
            detail="/api/agent/notes"
            onRetry={() => {
              loadNotes(notebookFilter || undefined, searchQuery || undefined);
              loadNotebooks();
            }}
          />
        </div>
      )}

      <div className="flex-1 overflow-y-auto">
        {!error && isLoading && notes.length === 0 && (
          <LoadingSkeleton variant="list" rows={5} />
        )}

        {!error && !isLoading && notes.length === 0 && !hasActiveFilters && (
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
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <polyline points="14,2 14,8 20,8" />
                <line x1="16" y1="13" x2="8" y2="13" />
                <line x1="16" y1="17" x2="8" y2="17" />
                <polyline points="10,9 9,9 8,9" />
              </svg>
            }
            title="No notes yet"
            description="Create a note or ask the agent to generate one."
            cta={{ label: "New note", onClick: handleNewNote }}
          />
        )}

        {!error && !isLoading && notes.length === 0 && hasActiveFilters && (
          <EmptyState
            title="No notes match your filters"
            description="Try a different notebook or search term."
            cta={{
              label: "Clear filters",
              onClick: () => {
                clearFilters();
                loadNotes(undefined, undefined);
              },
            }}
          />
        )}

        {notes.map((note: NoteSummary) => (
          <div
            key={note.id}
            onMouseEnter={() => setHoveredId(note.id)}
            onMouseLeave={() => setHoveredId(null)}
            onClick={() => handleSelectNote(note.id)}
            className="px-3 py-2.5 border-b border-navy-800 hover:bg-navy-800 transition-colors cursor-pointer"
          >
            <div className="flex items-start justify-between gap-1">
              <p className="text-sm text-parsnip-text font-medium line-clamp-1 flex-1">
                {note.title || "Untitled"}
              </p>
              {hoveredId === note.id && (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleDelete(note.id);
                  }}
                  className="shrink-0 text-parsnip-muted hover:text-red-400 transition-colors p-0.5 rounded"
                  title="Delete note"
                >
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="3,6 5,6 21,6" />
                    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                  </svg>
                </button>
              )}
            </div>

            {note.notebook_title && (
              <p className="text-[10px] text-parsnip-muted mt-0.5">
                {note.notebook_title}
              </p>
            )}

            <div className="flex items-center gap-1.5 mt-1 flex-wrap">
              {note.tags.slice(0, 3).map((tag) => (
                <span
                  key={tag}
                  className="text-[10px] px-1.5 py-0.5 rounded bg-navy-700 text-parsnip-teal"
                >
                  {tag}
                </span>
              ))}
              {note.tags.length > 3 && (
                <span className="text-[10px] text-parsnip-muted">
                  +{note.tags.length - 3}
                </span>
              )}
            </div>

            <div className="flex items-center mt-1.5">
              <span className="text-[10px] text-parsnip-muted">
                {formatTime(note.updated_at || note.created_at)}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
