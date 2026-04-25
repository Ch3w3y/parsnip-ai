"use client";

import { usePanelStore, type LeftPanelType } from "../stores/panel-store";
import { ThreadList } from "./ThreadList";
import { MemoryBrowser } from "./MemoryBrowser";
import { NotesBrowser } from "./NotesBrowser";
import { Tabs, TabsList, TabsTrigger } from "./ui/tabs";

function ChatBubbleIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
      <line x1="8" y1="9" x2="8.01" y2="9" />
      <line x1="12" y1="9" x2="12.01" y2="9" />
      <line x1="16" y1="9" x2="16.01" y2="9" />
    </svg>
  );
}

function BrainIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <circle cx="12" cy="8" r="2" fill="currentColor" />
      <circle cx="8" cy="14" r="1.5" fill="currentColor" />
      <circle cx="16" cy="14" r="1.5" fill="currentColor" />
      <line x1="12" y1="10" x2="12" y2="12" />
      <line x1="9" y1="13" x2="12" y2="12" />
      <line x1="15" y1="13" x2="12" y2="12" />
    </svg>
  );
}

function PencilSquareIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
      <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
    </svg>
  );
}

const TABS: { id: LeftPanelType; icon: typeof ChatBubbleIcon; label: string }[] = [
  { id: "threads", icon: ChatBubbleIcon, label: "Threads" },
  { id: "memories", icon: BrainIcon, label: "Memories" },
  { id: "notes", icon: PencilSquareIcon, label: "Notes" },
];

export function LeftSidebar() {
  const leftPanel = usePanelStore((s) => s.leftPanel);
  const setLeftPanel = usePanelStore((s) => s.setLeftPanel);

  return (
    <div className="flex flex-col h-full bg-navy-900 overflow-hidden">
      <Tabs value={leftPanel} onValueChange={(value) => setLeftPanel(value as LeftPanelType)}>
      <TabsList className="flex h-auto justify-start rounded-none border-b border-border bg-transparent px-1">
        {TABS.map((tab) => {
          const Icon = tab.icon;
          return (
            <TabsTrigger
              key={tab.id}
              value={tab.id}
              className="border-b-2 border-transparent p-2.5 text-muted-foreground data-[state=active]:border-primary data-[state=active]:text-primary"
              title={tab.label}
            >
              <Icon />
            </TabsTrigger>
          );
        })}
      </TabsList>
      </Tabs>

      <div className="flex-1 overflow-hidden">
        {leftPanel === "threads" && <ThreadList />}
        {leftPanel === "memories" && <MemoryBrowser />}
        {leftPanel === "notes" && <NotesBrowser />}
      </div>
    </div>
  );
}
