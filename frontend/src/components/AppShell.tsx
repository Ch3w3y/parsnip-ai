"use client";

import { useCallback, useEffect } from "react";
import {
  Group,
  Panel,
  Separator,
  usePanelRef,
} from "react-resizable-panels";
import type { PanelSize } from "react-resizable-panels";
import { usePanelStore } from "../stores/panel-store";
import { Header } from "./Header";
import { LeftSidebar } from "./LeftSidebar";
import { RightSidebar } from "./RightSidebar";
import { Thread } from "./assistant-ui/thread";
import { ToolBoundary } from "./tools/ToolBoundary";

export function AppShell() {
  const leftPanel = usePanelStore((s) => s.leftPanel);
  const rightPanel = usePanelStore((s) => s.rightPanel);
  const setLeftPanelWidth = usePanelStore((s) => s.setLeftPanelWidth);
  const setRightPanelWidth = usePanelStore((s) => s.setRightPanelWidth);

  const leftPanelRef = usePanelRef();
  const rightPanelRef = usePanelRef();

  const leftCollapsed = leftPanel === "closed";
  const rightCollapsed = rightPanel === "closed";

  useEffect(() => {
    const handle = leftPanelRef.current;
    if (!handle) return;
    if (leftCollapsed) {
      handle.collapse();
    } else if (handle.isCollapsed()) {
      handle.expand();
    }
  }, [leftCollapsed, leftPanelRef]);

  useEffect(() => {
    const handle = rightPanelRef.current;
    if (!handle) return;
    if (rightCollapsed) {
      handle.collapse();
    } else if (handle.isCollapsed()) {
      handle.expand();
    }
  }, [rightCollapsed, rightPanelRef]);

  const handleLeftResize = useCallback(
    (panelSize: PanelSize) => {
      setLeftPanelWidth(Math.round(panelSize.inPixels));
    },
    [setLeftPanelWidth]
  );

  const handleRightResize = useCallback(
    (panelSize: PanelSize) => {
      setRightPanelWidth(Math.round(panelSize.inPixels));
    },
    [setRightPanelWidth]
  );

  return (
    <div className="flex flex-col h-screen bg-navy-950 overflow-hidden">
      <Header />
      <div className="accent-line" />
      <Group orientation="horizontal" className="flex-1">
        <Panel
          id="left"
          defaultSize="20%"
          minSize="14%"
          maxSize="28%"
          collapsible
          collapsedSize={0}
          onResize={handleLeftResize}
          panelRef={leftPanelRef}
        >
          {!leftCollapsed && <LeftSidebar />}
        </Panel>

        <Separator className="w-[2px] bg-navy-600 hover:bg-parsnip-teal/50 transition-colors duration-200" />

        <Panel id="center" minSize="30%">
          <div className="flex flex-col h-full bg-navy-950 overflow-hidden">
            <ToolBoundary>
              <Thread />
            </ToolBoundary>
          </div>
        </Panel>

        <Separator className="w-[2px] bg-navy-600 hover:bg-parsnip-teal/50 transition-colors duration-200" />

        <Panel
          id="right"
          defaultSize="26%"
          minSize="20%"
          maxSize="35%"
          collapsible
          collapsedSize={0}
          onResize={handleRightResize}
          panelRef={rightPanelRef}
        >
          {!rightCollapsed && <RightSidebar />}
        </Panel>
      </Group>
    </div>
  );
}