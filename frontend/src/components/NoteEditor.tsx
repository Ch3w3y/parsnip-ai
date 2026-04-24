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
import { selectIsLoadingNote, useNoteStore } from "../stores/note-store";

interface NoteEditorProps {
  noteId?: string;
  initialContent?: string;
  onSave: (content: string) => Promise<void>;
  readOnly?: boolean;
}

export function NoteEditor({
  noteId,
  initialContent = "",
  onSave,
  readOnly = false,
}: NoteEditorProps) {
  const isLoadingNote = useNoteStore(selectIsLoadingNote);
  const [isDirty, setIsDirty] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
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
      setIsDirty(false);
    } finally {
      setIsSaving(false);
    }
  }, [editor, isDirty, isSaving, onSave]);

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
        handleSave();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [handleSave, readOnly]);

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
        {!readOnly && (
          <button
            type="button"
            onClick={handleSave}
            disabled={!isDirty || isSaving}
            className={`
              text-xs px-2.5 py-1 rounded font-medium transition-colors duration-150
              ${isDirty && !isSaving
                ? "bg-parsnip-teal text-navy-950 hover:brightness-110"
                : "bg-navy-700 text-parsnip-muted cursor-not-allowed"
              }
            `}
          >
            {isSaving ? "Saving…" : "Save"}
          </button>
        )}
      </div>

      {!readOnly && <NoteToolbar editor={editor} />}

      <div className="flex-1 overflow-y-auto">
        <EditorContent editor={editor} />
      </div>
    </div>
  );
}
