"use client";

import { type Editor } from "@tiptap/react";

// ── Inline SVG icons (16×16, stroke-based, matches LeftSidebar pattern) ────

function IconBold() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M6 4h8a4 4 0 0 1 4 4 4 4 0 0 1-4 4H6z" />
      <path d="M6 12h9a4 4 0 0 1 4 4 4 4 0 0 1-4 4H6z" />
    </svg>
  );
}

function IconItalic() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="19" y1="4" x2="10" y2="4" />
      <line x1="14" y1="20" x2="5" y2="20" />
      <line x1="12" y1="4" x2="8" y2="20" />
    </svg>
  );
}

function IconStrikethrough() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M16 4H9a3 3 0 0 0 0 6h6a3 3 0 0 1 0 6H8" />
      <line x1="4" y1="12" x2="20" y2="12" />
    </svg>
  );
}

function IconCode() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="16 18 22 12 16 6" />
      <polyline points="8 6 2 12 8 18" />
    </svg>
  );
}

function IconHeading1() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 12h8" />
      <path d="M4 18V6" />
      <path d="M12 18V6" />
      <path d="M17 18v-8l4 4" />
    </svg>
  );
}

function IconHeading2() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 12h8" />
      <path d="M4 18V6" />
      <path d="M12 18V6" />
      <path d="M17 12h4" />
      <path d="M17 18h4" />
      <path d="M21 12v6" />
    </svg>
  );
}

function IconHeading3() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 12h8" />
      <path d="M4 18V6" />
      <path d="M12 18V6" />
      <path d="M18 12h-2l1.5-2.5A2 2 0 0 0 19 7a2 2 0 0 0-4 0" />
    </svg>
  );
}

function IconBulletList() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="9" y1="6" x2="20" y2="6" />
      <line x1="9" y1="12" x2="20" y2="12" />
      <line x1="9" y1="18" x2="20" y2="18" />
      <circle cx="5" cy="6" r="1" fill="currentColor" />
      <circle cx="5" cy="12" r="1" fill="currentColor" />
      <circle cx="5" cy="18" r="1" fill="currentColor" />
    </svg>
  );
}

function IconOrderedList() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="10" y1="6" x2="20" y2="6" />
      <line x1="10" y1="12" x2="20" y2="12" />
      <line x1="10" y1="18" x2="20" y2="18" />
      <text x="3" y="8" fontSize="7" fill="currentColor" stroke="none" fontFamily="sans-serif">1</text>
      <text x="3" y="14" fontSize="7" fill="currentColor" stroke="none" fontFamily="sans-serif">2</text>
      <text x="3" y="20" fontSize="7" fill="currentColor" stroke="none" fontFamily="sans-serif">3</text>
    </svg>
  );
}

function IconTaskList() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="5" width="6" height="6" rx="1" />
      <path d="M6 8l1.5 1.5L10 6" />
      <line x1="13" y1="8" x2="20" y2="8" />
      <rect x="3" y="14" width="6" height="6" rx="1" />
      <line x1="13" y1="17" x2="20" y2="17" />
    </svg>
  );
}

function IconBlockquote() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 21c3 0 7-1 7-8V5c0-1.25-.756-2-2-2H4c-1.25 0-2 .75-2 1.972V11c0 1.25.75 2 2 2h1c0 4 0 7-2 8" />
      <path d="M15 21c3 0 7-1 7-8V5c0-1.25-.757-2-2-2h-4c-1.25 0-2 .75-2 1.972V11c0 1.25.75 2 2 2h1c0 4 0 7-2 8" />
    </svg>
  );
}

function IconCodeBlock() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <polyline points="9 8 6 11 9 14" />
      <polyline points="15 8 18 11 15 14" />
    </svg>
  );
}

function IconTable() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <line x1="3" y1="9" x2="21" y2="9" />
      <line x1="3" y1="15" x2="21" y2="15" />
      <line x1="9" y1="3" x2="9" y2="21" />
      <line x1="15" y1="3" x2="15" y2="21" />
    </svg>
  );
}

function IconLink() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
      <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
    </svg>
  );
}

function IconImage() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <circle cx="8.5" cy="8.5" r="1.5" />
      <polyline points="21 15 16 10 5 21" />
    </svg>
  );
}

function IconHighlight() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" />
    </svg>
  );
}

// ── Separator ──────────────────────────────────────────────────────────────

function ToolbarSeparator() {
  return <div className="w-px h-5 bg-navy-600 mx-0.5" />;
}

// ── Toolbar button ─────────────────────────────────────────────────────────

interface ToolbarButtonProps {
  icon: React.ReactNode;
  label: string;
  isActive?: boolean;
  disabled?: boolean;
  onClick: () => void;
}

function ToolbarButton({ icon, label, isActive, disabled, onClick }: ToolbarButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={label}
      className={`
        flex items-center justify-center w-7 h-7 rounded transition-colors duration-100
        ${isActive
          ? "bg-navy-600 text-parsnip-teal ring-1 ring-parsnip-teal"
          : "text-parsnip-muted hover:bg-navy-700 hover:text-parsnip-text"
        }
        disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent disabled:hover:text-parsnip-muted
      `}
    >
      {icon}
    </button>
  );
}

// ── Main toolbar component ─────────────────────────────────────────────────

interface NoteToolbarProps {
  editor: Editor;
}

export function NoteToolbar({ editor }: NoteToolbarProps) {
  const isActive = (name: string, attrs?: Record<string, unknown>) =>
    editor.isActive(name, attrs);

  const toggleHeading = (level: 1 | 2 | 3 | 4 | 5 | 6) => {
    editor.chain().focus().toggleHeading({ level }).run();
  };

  return (
    <div className="flex flex-wrap items-center gap-[1px] bg-navy-800 border-b border-navy-600 px-1.5 py-1">
      {/* Text style group */}
      <ToolbarButton
        icon={<IconBold />}
        label="Bold"
        isActive={isActive("bold")}
        onClick={() => editor.chain().focus().toggleBold().run()}
      />
      <ToolbarButton
        icon={<IconItalic />}
        label="Italic"
        isActive={isActive("italic")}
        onClick={() => editor.chain().focus().toggleItalic().run()}
      />
      <ToolbarButton
        icon={<IconStrikethrough />}
        label="Strikethrough"
        isActive={isActive("strike")}
        onClick={() => editor.chain().focus().toggleStrike().run()}
      />
      <ToolbarButton
        icon={<IconCode />}
        label="Code"
        isActive={isActive("code")}
        onClick={() => editor.chain().focus().toggleCode().run()}
      />

      <ToolbarSeparator />

      {/* Heading group */}
      <ToolbarButton
        icon={<IconHeading1 />}
        label="Heading 1"
        isActive={isActive("heading", { level: 1 })}
        onClick={() => toggleHeading(1)}
      />
      <ToolbarButton
        icon={<IconHeading2 />}
        label="Heading 2"
        isActive={isActive("heading", { level: 2 })}
        onClick={() => toggleHeading(2)}
      />
      <ToolbarButton
        icon={<IconHeading3 />}
        label="Heading 3"
        isActive={isActive("heading", { level: 3 })}
        onClick={() => toggleHeading(3)}
      />

      <ToolbarSeparator />

      {/* List group */}
      <ToolbarButton
        icon={<IconBulletList />}
        label="Bullet List"
        isActive={isActive("bulletList")}
        onClick={() => editor.chain().focus().toggleBulletList().run()}
      />
      <ToolbarButton
        icon={<IconOrderedList />}
        label="Ordered List"
        isActive={isActive("orderedList")}
        onClick={() => editor.chain().focus().toggleOrderedList().run()}
      />
      <ToolbarButton
        icon={<IconTaskList />}
        label="Task List"
        isActive={isActive("taskList")}
        onClick={() => editor.chain().focus().toggleTaskList().run()}
      />

      <ToolbarSeparator />

      {/* Block group */}
      <ToolbarButton
        icon={<IconBlockquote />}
        label="Blockquote"
        isActive={isActive("blockquote")}
        onClick={() => editor.chain().focus().toggleBlockquote().run()}
      />
      <ToolbarButton
        icon={<IconCodeBlock />}
        label="Code Block"
        isActive={isActive("codeBlock")}
        onClick={() => editor.chain().focus().toggleCodeBlock().run()}
      />
      <ToolbarButton
        icon={<IconTable />}
        label="Insert Table"
        onClick={() =>
          editor
            .chain()
            .focus()
            .insertTable({ rows: 3, cols: 3, withHeaderRow: true })
            .run()
        }
      />

      <ToolbarSeparator />

      {/* Insert group */}
      <ToolbarButton
        icon={<IconLink />}
        label="Add Link"
        isActive={isActive("link")}
        onClick={() => {
          const url = window.prompt("URL:");
          if (url) {
            editor.chain().focus().setLink({ href: url }).run();
          } else if (isActive("link")) {
            editor.chain().focus().unsetLink().run();
          }
        }}
      />
      <ToolbarButton
        icon={<IconImage />}
        label="Add Image"
        onClick={() => {
          const url = window.prompt("Image URL:");
          if (url) {
            editor.chain().focus().setImage({ src: url }).run();
          }
        }}
      />
      <ToolbarButton
        icon={<IconHighlight />}
        label="Highlight"
        isActive={isActive("highlight")}
        onClick={() => editor.chain().focus().toggleHighlight().run()}
      />
    </div>
  );
}