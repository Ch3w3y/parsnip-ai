import { create } from "zustand";
import { subscribeWithSelector } from "zustand/middleware";
import { persist } from "zustand/middleware";

export type LeftPanelType = "threads" | "memories" | "kb" | "notes" | "closed";
export type RightPanelType = "stats" | "outputs" | "note" | "closed";

interface PanelState {
  leftPanel: LeftPanelType;
  rightPanel: RightPanelType;
  previousLeftPanel: LeftPanelType;
  previousRightPanel: RightPanelType;
  leftPanelWidth: number;
  rightPanelWidth: number;
}

interface PanelActions {
  setLeftPanel: (panel: LeftPanelType) => void;
  setRightPanel: (panel: RightPanelType) => void;
  setLeftPanelWidth: (width: number) => void;
  setRightPanelWidth: (width: number) => void;
  toggleLeftCollapsed: () => void;
  toggleRightCollapsed: () => void;
}

type PanelStore = PanelState & PanelActions;

const DEFAULT_LEFT_WIDTH = 280;
const DEFAULT_RIGHT_WIDTH = 360;

export const usePanelStore = create<PanelStore>()(
  subscribeWithSelector(
    persist(
      (set, get) => ({
        leftPanel: "threads",
        rightPanel: "closed",
        previousLeftPanel: "threads" as LeftPanelType,
        previousRightPanel: "stats" as RightPanelType,
        leftPanelWidth: DEFAULT_LEFT_WIDTH,
        rightPanelWidth: DEFAULT_RIGHT_WIDTH,

        setLeftPanel: (panel: LeftPanelType) => {
          if (panel === "closed") {
            set({ leftPanel: "closed" });
          } else {
            set({ leftPanel: panel, previousLeftPanel: panel });
          }
        },

        setRightPanel: (panel: RightPanelType) => {
          if (panel === "closed") {
            set({ rightPanel: "closed" });
          } else {
            set({ rightPanel: panel, previousRightPanel: panel });
          }
        },

        setLeftPanelWidth: (width: number) => {
          set({ leftPanelWidth: width });
        },

        setRightPanelWidth: (width: number) => {
          set({ rightPanelWidth: width });
        },

        toggleLeftCollapsed: () => {
          set((state) => {
            if (state.leftPanel === "closed") {
              return { leftPanel: state.previousLeftPanel || "threads" };
            }
            return { leftPanel: "closed" as LeftPanelType };
          });
        },

        toggleRightCollapsed: () => {
          set((state) => {
            if (state.rightPanel === "closed") {
              return { rightPanel: state.previousRightPanel || "stats" };
            }
            return { rightPanel: "closed" as RightPanelType };
          });
        },
      }),
      {
        name: "panel-store",
        partialize: (state) => ({ leftPanelWidth: state.leftPanelWidth, rightPanelWidth: state.rightPanelWidth }),
      }
    )
  )
);

export const selectLeftPanel = (state: PanelStore) => state.leftPanel;
export const selectRightPanel = (state: PanelStore) => state.rightPanel;
export const selectLeftPanelWidth = (state: PanelStore) => state.leftPanelWidth;
export const selectRightPanelWidth = (state: PanelStore) => state.rightPanelWidth;
