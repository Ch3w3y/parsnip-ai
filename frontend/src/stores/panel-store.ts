import { create } from "zustand";
import { subscribeWithSelector } from "zustand/middleware";
import { persist } from "zustand/middleware";

export type LeftPanelType = "threads" | "memories" | "notes" | "closed";
export type RightPanelType = "stats" | "outputs" | "note" | "closed";
export type CenterView = "thread" | "notebook" | "welcome";

interface PanelState {
  leftPanel: LeftPanelType;
  rightPanel: RightPanelType;
  centerView: CenterView;
  previousCenterView: CenterView;
  previousLeftPanel: LeftPanelType;
  previousRightPanel: RightPanelType;
  leftPanelWidth: number;
  rightPanelWidth: number;
}

interface PanelActions {
  setLeftPanel: (panel: LeftPanelType) => void;
  setRightPanel: (panel: RightPanelType) => void;
  setCenterView: (view: CenterView) => void;
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
        centerView: "thread",
        previousCenterView: "thread" as CenterView,
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

        setCenterView: (view: CenterView) => {
          set({ previousCenterView: get().centerView, centerView: view });
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
        partialize: (state) => ({ leftPanelWidth: state.leftPanelWidth, rightPanelWidth: state.rightPanelWidth, centerView: state.centerView }),
      }
    )
  )
);

export const selectLeftPanel = (state: PanelStore) => state.leftPanel;
export const selectRightPanel = (state: PanelStore) => state.rightPanel;
export const selectCenterView = (state: PanelStore) => state.centerView;
export const selectPreviousCenterView = (state: PanelStore) => state.previousCenterView;
export const selectLeftPanelWidth = (state: PanelStore) => state.leftPanelWidth;
export const selectRightPanelWidth = (state: PanelStore) => state.rightPanelWidth;
