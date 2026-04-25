"use client";

import { usePanelStore, type RightPanelType } from "../stores/panel-store";
import {
  selectCurrentNote,
  selectCurrentNoteId,
  selectError,
  useNoteStore,
} from "../stores/note-store";
import { KBStatsPanel } from "./KBStatsPanel";
import { NoteEditor } from "./NoteEditor";
import { OutputsPanel } from "./OutputsPanel";
import { EmptyState } from "./ui/EmptyState";
import { ErrorBanner } from "./ui/ErrorBanner";
import { Tabs, TabsList, TabsTrigger } from "./ui/tabs";

function ChartBarIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="20" x2="18" y2="10" />
      <line x1="12" y1="20" x2="12" y2="4" />
      <line x1="6" y1="20" x2="6" y2="14" />
    </svg>
  );
}

function PhotoIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <circle cx="8.5" cy="8.5" r="1.5" />
      <polyline points="21,15 16,10 5,21" />
    </svg>
  );
}

function NoteIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14,2 14,8 20,8" />
      <line x1="16" y1="13" x2="8" y2="13" />
      <line x1="16" y1="17" x2="8" y2="17" />
    </svg>
  );
}

const TABS: { id: RightPanelType; icon: typeof ChartBarIcon; label: string }[] = [
  { id: "stats", icon: ChartBarIcon, label: "Stats" },
  { id: "outputs", icon: PhotoIcon, label: "Outputs" },
  { id: "note", icon: NoteIcon, label: "Note" },
];

function NotePanel() {
  const currentNote = useNoteStore(selectCurrentNote);
  const currentNoteId = useNoteStore(selectCurrentNoteId);
  const error = useNoteStore(selectError);
  const loadNote = useNoteStore((s) => s.loadNote);
  const updateNote = useNoteStore((s) => s.updateNote);

  if (!currentNoteId) {
    return (
      <EmptyState
        title="No note selected"
        description="Select a note from the Notes panel to edit it here."
      />
    );
  }

  return (
    <div className="flex h-full flex-col overflow-hidden p-3">
      <div className="mb-2 min-w-0">
        <div className="truncate text-sm font-semibold text-parsnip-text">
          {currentNote?.title || "Untitled Note"}
        </div>
        {currentNote?.notebook_title ? (
          <div className="truncate text-[10px] text-parsnip-muted">
            {currentNote.notebook_title}
          </div>
        ) : null}
      </div>

      {error && (
        <div className="mb-2">
          <ErrorBanner
            message={error}
            detail={`/api/agent/notes/${currentNoteId}`}
            onRetry={() => loadNote(currentNoteId)}
          />
        </div>
      )}

      <div className="min-h-0 flex-1">
        <NoteEditor
          key={currentNoteId}
          noteId={currentNoteId}
          initialContent={currentNote?.content ?? ""}
          onSave={(content) => updateNote(currentNoteId, { content })}
        />
      </div>
    </div>
  );
}

export function RightSidebar() {
  const rightPanel = usePanelStore((s) => s.rightPanel);
  const setRightPanel = usePanelStore((s) => s.setRightPanel);

  return (
    <div className="flex flex-col h-full bg-navy-900 overflow-hidden">
      <Tabs value={rightPanel} onValueChange={(value) => setRightPanel(value as RightPanelType)}>
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
        {rightPanel === "stats" && <KBStatsPanel />}
        {rightPanel === "outputs" && <OutputsPanel />}
        {rightPanel === "note" && <NotePanel />}
      </div>
    </div>
  );
}
