import { create } from "zustand";
import { subscribeWithSelector } from "zustand/middleware";

export const MEMORY_CATEGORIES = ["user_prefs", "facts", "decisions", "project_context", "people"] as const;
export const IMPORTANCE_RANGE = { min: 1, max: 5, labels: ["nice-to-know", "useful", "notable", "important", "critical"] } as const;

export interface MemoryItem {
  id: number;
  category: string;
  content: string;
  importance: number;
  created_at: string | null;
  updated_at: string | null;
}

interface MemoryState {
  memories: MemoryItem[];
  isLoading: boolean;
  deletingIds: Set<number>;
  deleteErrors: Record<number, string>;
  categoryFilter: string;
  importanceFilter: number;
  searchQuery: string;
  error: string | null;
}

interface MemoryActions {
  loadMemories: () => Promise<void>;
  deleteMemory: (id: number) => Promise<void>;
  setCategoryFilter: (category: string) => void;
  setImportanceFilter: (min: number) => void;
  setSearchQuery: (query: string) => void;
  clearFilters: () => void;
}

type MemoryStore = MemoryState & MemoryActions;

export const useMemoryStore = create<MemoryStore>()(
  subscribeWithSelector((set, get) => ({
    memories: [],
    isLoading: false,
    deletingIds: new Set(),
    deleteErrors: {},
    categoryFilter: "",
    importanceFilter: 0,
    searchQuery: "",
    error: null,

    loadMemories: async () => {
      set({ isLoading: true, error: null });
      try {
        const { categoryFilter, importanceFilter, searchQuery } = get();
        const params = new URLSearchParams();
        if (categoryFilter) params.set("category", categoryFilter);
        if (importanceFilter > 0) params.set("min_importance", String(importanceFilter));
        if (searchQuery.trim()) params.set("search", searchQuery.trim());
        params.set("limit", "50");

        const url = `/api/agent/memories?${params.toString()}`;
        const res = await fetch(url);
        
        if (res.ok) {
          const data = await res.json();
          set({ memories: data.memories || [] });
        } else {
          set({ error: `Failed to load memories: ${res.status}` });
        }
      } catch (err) {
        set({ error: "Network error loading memories" });
      } finally {
        set({ isLoading: false });
      }
    },

    deleteMemory: async (id: number) => {
      set((state) => {
        const deletingIds = new Set(state.deletingIds);
        deletingIds.add(id);
        return {
          deletingIds,
          deleteErrors: { ...state.deleteErrors, [id]: "" },
          error: null,
        };
      });
      try {
        const res = await fetch(`/api/agent/memories/${id}`, { method: "DELETE" });
        if (res.ok) {
          set((state) => {
            const deletingIds = new Set(state.deletingIds);
            deletingIds.delete(id);
            const deleteErrors = { ...state.deleteErrors };
            delete deleteErrors[id];
            return {
              memories: state.memories.filter((m) => m.id !== id),
              deletingIds,
              deleteErrors,
            };
          });
        } else {
          set((state) => {
            const deletingIds = new Set(state.deletingIds);
            deletingIds.delete(id);
            return {
              deletingIds,
              deleteErrors: {
                ...state.deleteErrors,
                [id]: `Failed to delete memory: ${res.status}`,
              },
            };
          });
        }
      } catch {
        set((state) => {
          const deletingIds = new Set(state.deletingIds);
          deletingIds.delete(id);
          return {
            deletingIds,
            deleteErrors: {
              ...state.deleteErrors,
              [id]: "Network error deleting memory",
            },
          };
        });
      }
    },

    setCategoryFilter: (category: string) => {
      set({ categoryFilter: category });
    },

    setImportanceFilter: (min: number) => {
      set({ importanceFilter: min });
    },

    setSearchQuery: (query: string) => {
      set({ searchQuery: query });
    },

    clearFilters: () => {
      set({ categoryFilter: "", importanceFilter: 0, searchQuery: "" });
    },
  }))
);

export const selectMemories = (state: MemoryStore) => state.memories;

export const selectIsLoading = (state: MemoryStore) => state.isLoading;
export const selectDeletingIds = (state: MemoryStore) => state.deletingIds;
export const selectDeleteErrors = (state: MemoryStore) => state.deleteErrors;
export const selectError = (state: MemoryStore) => state.error;
export const selectCategoryFilter = (state: MemoryStore) => state.categoryFilter;
export const selectImportanceFilter = (state: MemoryStore) => state.importanceFilter;
export const selectSearchQuery = (state: MemoryStore) => state.searchQuery;
export const selectAllMemories = (state: MemoryStore) => state.memories;
