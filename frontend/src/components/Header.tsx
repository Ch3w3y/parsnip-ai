"use client";

import { usePanelStore, type LeftPanelType, type RightPanelType } from "../stores/panel-store";
import { HeaderKBWidget } from "./HeaderKBWidget";
import { PanelIconButton } from "./ui/panel";

const LEFT_CYCLE: LeftPanelType[] = ["threads", "memories", "closed"];
const RIGHT_CYCLE: RightPanelType[] = ["stats", "outputs", "closed"];

export function Header() {
  const leftPanel = usePanelStore((s) => s.leftPanel);
  const rightPanel = usePanelStore((s) => s.rightPanel);
  const setLeftPanel = usePanelStore((s) => s.setLeftPanel);
  const setRightPanel = usePanelStore((s) => s.setRightPanel);

  const cycleLeft = () => {
    const idx = LEFT_CYCLE.indexOf(leftPanel);
    setLeftPanel(LEFT_CYCLE[(idx + 1) % LEFT_CYCLE.length]);
  };

  const cycleRight = () => {
    const idx = RIGHT_CYCLE.indexOf(rightPanel);
    setRightPanel(RIGHT_CYCLE[(idx + 1) % RIGHT_CYCLE.length]);
  };

  return (
    <header className="flex items-center justify-between px-5 py-3 bg-navy-900 border-b border-navy-600 select-none">
      <div className="flex items-center gap-3">
        <div className="w-8 h-8 rounded-lg bg-brand-gradient flex items-center justify-center">
          <span className="text-white font-bold text-sm">P</span>
        </div>
        <h1 className="text-lg font-bold gradient-text">parsnip</h1>
        <span className="text-parsnip-muted text-xs hidden sm:inline">
          Grounded research &amp; analysis
        </span>
      </div>

      <div className="flex-1 flex justify-center">
        <HeaderKBWidget />
      </div>

      <div className="flex items-center gap-2">
        <PanelIconButton
          onClick={cycleLeft}
          className={`${
            leftPanel !== "closed"
              ? "text-parsnip-teal hover:bg-navy-800"
              : "text-parsnip-muted hover:text-parsnip-text hover:bg-navy-800"
          }`}
          label={`Left panel: ${leftPanel}`}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3" width="18" height="18" rx="2" />
            <line x1="9" y1="3" x2="9" y2="21" />
          </svg>
        </PanelIconButton>

        <PanelIconButton
          onClick={cycleRight}
          className={`${
            rightPanel !== "closed"
              ? "text-parsnip-teal hover:bg-navy-800"
              : "text-parsnip-muted hover:text-parsnip-text hover:bg-navy-800"
          }`}
          label={`Right panel: ${rightPanel}`}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3" width="18" height="18" rx="2" />
            <line x1="15" y1="3" x2="15" y2="21" />
          </svg>
        </PanelIconButton>

        <div className="w-px h-4 bg-navy-600 mx-1" />

        <div className="flex items-center gap-2 text-xs text-parsnip-muted">
          <span className="w-2 h-2 rounded-full bg-parsnip-teal pulse-dot" />
          Connected
        </div>
      </div>
    </header>
  );
}
