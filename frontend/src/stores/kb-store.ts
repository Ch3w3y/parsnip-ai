import { create } from "zustand";
import { subscribeWithSelector } from "zustand/middleware";

export interface KBStats {
  source: string;
  chunks: number;
  last_updated: string | null;
}

interface KBState {
  stats: KBStats[];
  ingestionStatus: Record<string, unknown> | null;
  isLoadingStats: boolean;
  isLoadingIngestion: boolean;
  error: string | null;
}

interface KBActions {
  loadStats: () => Promise<void>;
  loadIngestionStatus: () => Promise<void>;
}

type KBStore = KBState & KBActions;

export const useKBStore = create<KBStore>()(
  subscribeWithSelector((set) => ({
    stats: [],
    ingestionStatus: null,
    isLoadingStats: false,
    isLoadingIngestion: false,
    error: null,

    loadStats: async () => {
      set({ isLoadingStats: true, error: null });
      try {
        const res = await fetch("/api/agent/stats");
        if (res.ok) {
          const data = await res.json();
          set({ stats: data.knowledge_base || [] });
        } else {
          set({ error: `Failed to load stats: ${res.status}` });
        }
      } catch (err) {
        set({ error: "Network error loading stats" });
      } finally {
        set({ isLoadingStats: false });
      }
    },

    loadIngestionStatus: async () => {
      set({ isLoadingIngestion: true, error: null });
      try {
        const res = await fetch("/api/agent/ingestion/status");
        if (res.ok) {
          const data = await res.json();
          set({ ingestionStatus: data });
        } else {
          set({ error: `Failed to load ingestion status: ${res.status}` });
        }
      } catch (err) {
        set({ error: "Network error loading ingestion status" });
      } finally {
        set({ isLoadingIngestion: false });
      }
    },
  }))
);

export const selectStats = (state: KBStore) => state.stats;
export const selectIngestionStatus = (state: KBStore) => state.ingestionStatus;
export const selectIsLoadingStats = (state: KBStore) => state.isLoadingStats;
export const selectIsLoadingIngestion = (state: KBStore) => state.isLoadingIngestion;
export const selectError = (state: KBStore) => state.error;
