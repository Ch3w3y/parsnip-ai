import { create } from "zustand";
import { subscribeWithSelector } from "zustand/middleware";

// --- Types ---

export interface Resource {
  id: string;
  title?: string;
  mime?: string;
  size?: number;
}

export interface NoteSummary {
  id: string;
  title: string;
  notebook_id?: string;
  notebook_title?: string;
  tags: string[];
  created_at: string;
  updated_at: string;
}

export interface NoteDetail extends NoteSummary {
  content: string;
  resources: Resource[];
}

export interface Notebook {
  id: string;
  title: string;
  parent_id?: string;
  note_count: number;
}

// --- State ---

interface NoteState {
  notes: NoteSummary[];
  currentNoteId: string | null;
  currentNote: NoteDetail | null;
  notebooks: Notebook[];
  isLoading: boolean;
  isLoadingNote: boolean;
  searchQuery: string;
  notebookFilter: string;
  error: string | null;
}

// --- Actions ---

interface NoteActions {
  loadNotes: (notebookId?: string, search?: string) => Promise<void>;
  loadNote: (noteId: string) => Promise<void>;
  createNote: (title: string, content: string, notebookId?: string, tags?: string[]) => Promise<NoteDetail | null>;
  updateNote: (
    noteId: string,
    updates: {
      title?: string;
      content?: string;
      tags?: string[];
      notebook_id?: string;
    },
  ) => Promise<void>;
  deleteNote: (noteId: string) => Promise<void>;
  loadNotebooks: () => Promise<void>;
  setCurrentNoteId: (id: string | null) => void;
  setSearchQuery: (query: string) => void;
  setNotebookFilter: (notebookId: string) => void;
  clearFilters: () => void;
}

type NoteStore = NoteState & NoteActions;

// --- Store ---

export const useNoteStore = create<NoteStore>()(
  subscribeWithSelector((set, get) => ({
    notes: [],
    currentNoteId: null,
    currentNote: null,
    notebooks: [],
    isLoading: false,
    isLoadingNote: false,
    searchQuery: "",
    notebookFilter: "",
    error: null,

    loadNotes: async (notebookId?: string, search?: string) => {
      set({ isLoading: true, error: null });
      try {
        const params = new URLSearchParams();
        if (notebookId) params.set("notebook_id", notebookId);
        if (search) params.set("search", search);
        params.set("limit", "50");

        const qs = params.toString();
        const url = `/api/agent/notes${qs ? `?${qs}` : ""}`;
        const res = await fetch(url);

        if (res.ok) {
          const data = await res.json();
          set({ notes: data.notes || data || [] });
        } else {
          set({ error: `Failed to load notes: ${res.status}` });
        }
      } catch {
        set({ error: "Network error loading notes" });
      } finally {
        set({ isLoading: false });
      }
    },

    loadNote: async (noteId: string) => {
      set({ isLoadingNote: true, error: null });
      try {
        const res = await fetch(`/api/agent/notes/${encodeURIComponent(noteId)}`);
        if (res.ok) {
          const data = await res.json();
          set({ currentNote: data, currentNoteId: noteId });
        } else {
          set({ error: `Failed to load note: ${res.status}` });
        }
      } catch {
        set({ error: "Network error loading note" });
      } finally {
        set({ isLoadingNote: false });
      }
    },

    createNote: async (title: string, content: string, notebookId?: string, tags?: string[]) => {
      set({ error: null });
      try {
        const body: Record<string, unknown> = { title, content };
        if (notebookId) body.notebook_id = notebookId;
        if (tags && tags.length > 0) body.tags = tags;

        const res = await fetch("/api/agent/notes", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });

        if (res.ok) {
          const note = await res.json() as NoteDetail;
          // Optimistic: add to list immediately, then re-fetch
          set((state) => ({
            notes: [note, ...state.notes],
            currentNote: note,
            currentNoteId: note.id,
          }));
          // Re-fetch to ensure consistency
          void get().loadNotes(
            get().notebookFilter || undefined,
            get().searchQuery || undefined,
          );
          void get().loadNotebooks();
          return note;
        }
        set({ error: `Failed to create note: ${res.status}` });
        return null;
      } catch {
        set({ error: "Network error creating note" });
        return null;
      }
    },

    updateNote: async (
      noteId: string,
      updates: {
        title?: string;
        content?: string;
        tags?: string[];
        notebook_id?: string;
      },
    ) => {
      set({ error: null });
      try {
        // Optimistic update on currentNote
        set((state) => {
          if (state.currentNote && state.currentNote.id === noteId) {
            return {
              currentNote: { ...state.currentNote, ...updates },
            };
          }
          return {};
        });

        const res = await fetch(`/api/agent/notes/${encodeURIComponent(noteId)}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(updates),
        });

        if (res.ok) {
          // Re-fetch list to stay consistent
          void get().loadNotes(
            get().notebookFilter || undefined,
            get().searchQuery || undefined,
          );
          if ("notebook_id" in updates) {
            void get().loadNotebooks();
          }
        } else {
          set({ error: `Failed to update note: ${res.status}` });
        }
      } catch {
        set({ error: "Network error updating note" });
      }
    },

    deleteNote: async (noteId: string) => {
      set({ error: null });
      try {
        // Optimistic: remove from list immediately
        set((state) => ({
          notes: state.notes.filter((n) => n.id !== noteId),
          currentNoteId: state.currentNoteId === noteId ? null : state.currentNoteId,
          currentNote: state.currentNoteId === noteId ? null : state.currentNote,
        }));

        const res = await fetch(`/api/agent/notes/${encodeURIComponent(noteId)}`, {
          method: "DELETE",
        });

        if (res.ok) {
          void get().loadNotebooks();
        } else {
          // Rollback by re-fetching
          set({ error: `Failed to delete note: ${res.status}` });
          void get().loadNotes(
            get().notebookFilter || undefined,
            get().searchQuery || undefined,
          );
          void get().loadNotebooks();
        }
      } catch {
        set({ error: "Network error deleting note" });
        void get().loadNotes(
          get().notebookFilter || undefined,
          get().searchQuery || undefined,
        );
        void get().loadNotebooks();
      }
    },

    loadNotebooks: async () => {
      set({ error: null });
      try {
        const res = await fetch("/api/agent/notebooks");
        if (res.ok) {
          const data = await res.json();
          set({ notebooks: data.notebooks || data || [] });
        } else {
          set({ error: `Failed to load notebooks: ${res.status}` });
        }
      } catch {
        set({ error: "Network error loading notebooks" });
      }
    },

    setCurrentNoteId: (id: string | null) => {
      set({ currentNoteId: id });
      if (id) {
        get().loadNote(id);
      } else {
        set({ currentNote: null });
      }
    },

    setSearchQuery: (query: string) => {
      set({ searchQuery: query });
    },

    setNotebookFilter: (notebookId: string) => {
      set({ notebookFilter: notebookId });
    },

    clearFilters: () => {
      set({ searchQuery: "", notebookFilter: "" });
    },
  }))
);

// --- Selectors ---

export const selectNotes = (state: NoteStore) => {
  return state.notes;
};

export const selectIsLoading = (state: NoteStore) => state.isLoading;
export const selectIsLoadingNote = (state: NoteStore) => state.isLoadingNote;
export const selectError = (state: NoteStore) => state.error;
export const selectCurrentNote = (state: NoteStore) => state.currentNote;
export const selectCurrentNoteId = (state: NoteStore) => state.currentNoteId;
export const selectNotebooks = (state: NoteStore) => state.notebooks;
export const selectNotebookFilter = (state: NoteStore) => state.notebookFilter;
export const selectSearchQuery = (state: NoteStore) => state.searchQuery;
