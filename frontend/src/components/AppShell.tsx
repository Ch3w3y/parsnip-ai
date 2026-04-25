"use client";

import { useCallback, useEffect, useState } from "react";
import { usePanelRef } from "react-resizable-panels";
import type { PanelSize } from "react-resizable-panels";
import { usePanelStore, selectPreviousCenterView } from "../stores/panel-store";
import { useNoteStore } from "../stores/note-store";
import { Header } from "./Header";
import { LeftSidebar } from "./LeftSidebar";
import { RightSidebar } from "./RightSidebar";
import { Thread } from "./assistant-ui/thread";
import { WelcomeScreen } from "./WelcomeScreen";
import { NoteEditor } from "./NoteEditor";
import { ToolBoundary } from "./tools/ToolBoundary";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "./ui/resizable";

function CenterViewSwitcher() {
  const centerView = usePanelStore((s) => s.centerView);
  const currentNoteId = useNoteStore((s) => s.currentNoteId);
  const currentNote = useNoteStore((s) => s.currentNote);
  const updateNote = useNoteStore((s) => s.updateNote);
  const previousCenterView = usePanelStore(selectPreviousCenterView);
  const setCenterView = usePanelStore((s) => s.setCenterView);
  const [visible, setVisible] = useState(true);
  const [mountedView, setMountedView] = useState(centerView);

  useEffect(() => {
    if (centerView !== mountedView) {
      setVisible(false);
      const timer = setTimeout(() => {
        setMountedView(centerView);
        setVisible(true);
      }, 150);
      return () => clearTimeout(timer);
    }
  }, [centerView, mountedView]);

  useEffect(() => {
    setMountedView(centerView);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const renderContent = () => {
    switch (mountedView) {
      case "thread":
        return <Thread />;
      case "welcome":
        return <WelcomeScreen />;
      case "notebook":
        if (!currentNoteId) {
          return (
            <div className="flex items-center justify-center h-full text-parsnip-muted text-sm">
              Select a note to view
            </div>
          );
        }
         return (
          <NoteEditor
            noteId={currentNoteId}
            initialContent={currentNote?.content ?? ""}
            onSave={(content) => updateNote(currentNoteId, { content })}
            onBack={() => setCenterView(previousCenterView)}
          />
        );
      default:
        return <Thread />;
    }
  };

  return (
    <div
      className="flex flex-col h-full transition-all duration-200 ease-out"
      style={{
        opacity: visible ? 1 : 0,
        transform: visible ? "translateX(0)" : "translateX(8px)",
      }}
    >
      {renderContent()}
    </div>
  );
}

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
      <ResizablePanelGroup orientation="horizontal" className="flex-1">
        <ResizablePanel
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
        </ResizablePanel>

        <ResizableHandle className="w-[2px] bg-border hover:bg-primary/50 transition-colors duration-200" />

        <ResizablePanel id="center" minSize="30%">
          <div className="flex flex-col h-full bg-navy-950 overflow-hidden">
            <ToolBoundary>
              <CenterViewSwitcher />
            </ToolBoundary>
          </div>
        </ResizablePanel>

        <ResizableHandle className="w-[2px] bg-border hover:bg-primary/50 transition-colors duration-200" />

        <ResizablePanel
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
        </ResizablePanel>
      </ResizablePanelGroup>
    </div>
  );
}
