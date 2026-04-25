"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useEditor, EditorContent } from "@tiptap/react";
import { StarterKit } from "@tiptap/starter-kit";
import { Markdown } from "@tiptap/markdown";
import { CodeBlockLowlight } from "@tiptap/extension-code-block-lowlight";
import { Table } from "@tiptap/extension-table";
import { TableRow } from "@tiptap/extension-table-row";
import { TableCell } from "@tiptap/extension-table-cell";
import { TableHeader } from "@tiptap/extension-table-header";
import { TaskList } from "@tiptap/extension-task-list";
import { TaskItem } from "@tiptap/extension-task-item";
import { Highlight } from "@tiptap/extension-highlight";
import { Image } from "@tiptap/extension-image";
import { Link } from "@tiptap/extension-link";
import { Placeholder } from "@tiptap/extension-placeholder";
import { common, createLowlight } from "lowlight";

import { NoteToolbar } from "./NoteToolbar";
import { LoadingSkeleton } from "./ui/LoadingSkeleton";
import { Button } from "./ui/button";
import { Textarea } from "./ui/textarea";
import { selectIsLoadingNote, useNoteStore } from "../stores/note-store";

type ViewMode = "rich" | "raw";

interface NoteEditorProps {
  noteId?: string;
  initialContent?: string;
  onSave: (content: string) => Promise<void>;
  readOnly?: boolean;
  onBack?: () => void;
}

export function NoteEditor({
  noteId,
  initialContent = "",
  onSave,
  readOnly = false,
  onBack,
}: NoteEditorProps) {
  const isLoadingNote = useNoteStore(selectIsLoadingNote);
  const [isDirty, setIsDirty] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>("rich");
  const [rawContent, setRawContent] = useState(initialContent);
  const lastSavedContent = useRef(initialContent);

  const editor = useEditor({
    immediatelyRender: false,
    extensions: [
      StarterKit.configure({ codeBlock: false }),
      Markdown,
      CodeBlockLowlight.configure({ lowlight: createLowlight(common) }),
      Table.configure({ resizable: true }),
      TableRow,
      TableCell,
      TableHeader,
      TaskList,
      TaskItem.configure({ nested: true }),
      Highlight,
      Image,
      Link.configure({ openOnClick: false, autolink: true }),
      Placeholder.configure({
        placeholder: "Start writing…",
      }),
    ],
    content: initialContent,
    editable: !readOnly,
    onUpdate: ({ editor: ed }) => {
      const current = ed.getMarkdown();
      setIsDirty(current !== lastSavedContent.current);
    },
    editorProps: {
      attributes: {
        class: "note-editor-content outline-none",
      },
    },
  });

  useEffect(() => {
    if (!editor || initialContent === undefined) return;
    const currentMarkdown = editor.getMarkdown();
    if (currentMarkdown !== initialContent) {
      editor.commands.setContent(initialContent, {
        emitUpdate: false,
        contentType: "markdown",
      });
      lastSavedContent.current = initialContent;
      setRawContent(initialContent);
      setIsDirty(false);
    }
  }, [editor, initialContent]);

  useEffect(() => {
    if (editor) {
      editor.setEditable(!readOnly);
    }
  }, [editor, readOnly]);

  const handleSave = useCallback(async () => {
    if (!editor || !isDirty || isSaving) return;
    const markdown = editor.getMarkdown();
    setIsSaving(true);
    try {
      await onSave(markdown);
      lastSavedContent.current = markdown;
      setRawContent(markdown);
      setIsDirty(false);
    } finally {
      setIsSaving(false);
    }
  }, [editor, isDirty, isSaving, onSave]);

  const handleViewModeChange = useCallback(
    (mode: ViewMode) => {
      if (mode === viewMode) return;
      if (mode === "raw" && editor) {
        setRawContent(editor.getMarkdown());
      } else if (mode === "rich" && editor) {
        editor.commands.setContent(rawContent, {
          emitUpdate: false,
          contentType: "markdown",
        });
        const newMd = editor.getMarkdown();
        setIsDirty(newMd !== lastSavedContent.current);
      }
      setViewMode(mode);
    },
    [editor, viewMode, rawContent],
  );

  const handleRawChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      const value = e.target.value;
      setRawContent(value);
      setIsDirty(value !== lastSavedContent.current);
    },
    [],
  );

  const handleRawBlur = useCallback(async () => {
    if (!isDirty || isSaving) return;
    setIsSaving(true);
    try {
      await onSave(rawContent);
      lastSavedContent.current = rawContent;
      setIsDirty(false);
    } finally {
      setIsSaving(false);
    }
  }, [isDirty, isSaving, onSave, rawContent]);

  useEffect(() => {
    if (!editor || readOnly) return;
    const handleBlur = () => {
      if (isDirty) handleSave();
    };
    const dom = editor.view.dom;
    dom.addEventListener("blur", handleBlur);
    return () => dom.removeEventListener("blur", handleBlur);
  }, [editor, isDirty, handleSave, readOnly]);

  useEffect(() => {
    if (readOnly) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        if (viewMode === "raw") {
          handleRawBlur();
        } else {
          handleSave();
        }
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [handleSave, handleRawBlur, readOnly, viewMode]);

  if (isLoadingNote) {
    return <LoadingSkeleton variant="card" rows={5} />;
  }

  if (!editor) {
    return <LoadingSkeleton variant="card" rows={4} />;
  }

  return (
    <div className="flex flex-col bg-navy-950 rounded-lg overflow-hidden h-full">
      <div className="flex items-center justify-between px-3 py-1.5 bg-navy-900 border-b border-navy-700">
        <div className="flex items-center gap-2">
          {onBack && (
            <Button
              type="button"
              onClick={onBack}
              variant="ghost"
              size="icon-sm"
              className="text-muted-foreground hover:text-primary"
              title="Back"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="15,18 9,12 15,6" />
              </svg>
            </Button>
          )}
          {noteId && (
            <span className="text-xs text-parsnip-muted font-mono truncate max-w-[140px]">
              {noteId}
            </span>
          )}
          {isDirty && (
            <span
              className="w-2 h-2 rounded-full bg-parsnip-teal pulse-dot"
              title="Unsaved changes"
            />
          )}
        </div>
        <div className="flex items-center gap-2">
          {!readOnly && (
            <div className="flex items-center rounded-md overflow-hidden border border-navy-600">
              <Button
                type="button"
                onClick={() => handleViewModeChange("rich")}
                variant={viewMode === "rich" ? "default" : "ghost"}
                size="xs"
                className="h-6 rounded-none text-[10px]"
              >
                Rich
              </Button>
              <Button
                type="button"
                onClick={() => handleViewModeChange("raw")}
                variant={viewMode === "raw" ? "default" : "ghost"}
                size="xs"
                className="h-6 rounded-none text-[10px]"
              >
                Raw
              </Button>
            </div>
          )}
          {!readOnly && (
            <Button
              type="button"
              onClick={viewMode === "raw" ? handleRawBlur : handleSave}
              disabled={!isDirty || isSaving}
              size="xs"
            >
              {isSaving ? "Saving…" : "Save"}
            </Button>
          )}
        </div>
      </div>

      {!readOnly && viewMode === "rich" && <NoteToolbar editor={editor} />}

      <div className="flex-1 overflow-y-auto">
        {viewMode === "raw" ? (
          <Textarea
            className="h-full min-h-full resize-none rounded-none border-0 bg-navy-950 p-4 font-mono text-sm text-slate-200 focus-visible:ring-1"
            value={rawContent}
            onChange={handleRawChange}
            onBlur={handleRawBlur}
            spellCheck={false}
          />
        ) : (
          <EditorContent editor={editor} />
        )}
      </div>
    </div>
  );
}
