"use client";

import {
  ThreadPrimitive,
  ComposerPrimitive,
  MessagePrimitive,
  TextMessagePartComponent,
} from "@assistant-ui/react";
import { WelcomeScreen } from "../WelcomeScreen";
import { MarkdownRenderer } from "../MarkdownRenderer";
import { ToolCallBadge } from "../tools/ToolCallBadge";
import {
  useThreadStore,
  type SerializedMessage,
} from "../../stores/thread-store";

export function Thread() {
  return (
    <ThreadPrimitive.Root className="flex h-full flex-col">
      <ThreadPrimitive.Empty>
        <WelcomeScreen />
      </ThreadPrimitive.Empty>

      <ThreadPrimitive.Viewport className="flex-1 overflow-y-auto px-4 py-6 scroll-smooth">
        <ThreadPrimitive.Messages
          components={{
            UserMessage,
            AssistantMessage,
          }}
        />
      </ThreadPrimitive.Viewport>

      <div className="border-t border-navy-600 px-4 py-3">
        <ComposerPrimitive.Root className="flex items-end gap-2">
          <ComposerPrimitive.Input
            placeholder="Ask parsnip anything..."
            className="flex-1 resize-none rounded-lg border border-navy-600 bg-navy-800 px-4 py-3 text-sm text-parsnip-text placeholder:text-parsnip-muted focus:border-parsnip-teal focus:outline-none focus:ring-1 focus:ring-parsnip-teal/30"
            rows={1}
          />
          <ComposerPrimitive.Send className="rounded-lg bg-parsnip-teal/15 px-4 py-3 text-sm font-medium text-parsnip-teal hover:bg-parsnip-teal/25 transition-colors border border-parsnip-teal/30">
            Send
          </ComposerPrimitive.Send>
        </ComposerPrimitive.Root>
      </div>
    </ThreadPrimitive.Root>
  );
}

function UserMessage() {
  return (
    <div className="flex justify-end mb-4">
      <div className="max-w-[80%] rounded-2xl rounded-br-sm bg-parsnip-teal/15 px-4 py-2.5 text-sm text-parsnip-text">
        <MessagePrimitive.Content components={{ Text: UserMessageText }} />
      </div>
    </div>
  );
}

const UserMessageText: TextMessagePartComponent = ({ text }) => {
  return <div className="whitespace-pre-wrap">{text}</div>;
};

/**
 * Read the LAST assistant message from the Zustand store. Only meaningful
 * when rendered inside a <MessagePrimitive.If last> block, where we know
 * the rendered message corresponds to the last entry in currentMessages.
 */
function useLastAssistantMessage(): SerializedMessage | undefined {
  return useThreadStore((s) => {
    const last = s.currentMessages[s.currentMessages.length - 1];
    return last?.role === "assistant" ? last : undefined;
  });
}

function StreamingIndicator({ hasContent }: { hasContent: boolean }) {
  if (hasContent) {
    return (
      <span
        className="inline-block w-[2px] h-[1em] bg-parsnip-teal/80 align-middle ml-0.5 animate-pulse"
        aria-hidden="true"
      />
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1 py-1"
      role="status"
      aria-live="polite"
      aria-label="Assistant is thinking"
    >
      <span className="h-1.5 w-1.5 rounded-full bg-parsnip-teal/70 animate-pulse" />
      <span
        className="h-1.5 w-1.5 rounded-full bg-parsnip-teal/70 animate-pulse"
        style={{ animationDelay: "150ms" }}
      />
      <span
        className="h-1.5 w-1.5 rounded-full bg-parsnip-teal/70 animate-pulse"
        style={{ animationDelay: "300ms" }}
      />
      <span className="ml-1.5 text-[11px] text-parsnip-muted">thinking…</span>
    </span>
  );
}

function formatElapsed(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
}

function MessageFooter({
  meta,
}: {
  meta: NonNullable<SerializedMessage["meta"]>;
}) {
  const parts: string[] = [];
  if (meta.modelId) parts.push(meta.modelId);
  if (typeof meta.elapsedMs === "number")
    parts.push(formatElapsed(meta.elapsedMs));
  if (typeof meta.toolCount === "number" && meta.toolCount > 0) {
    parts.push(`${meta.toolCount} tool${meta.toolCount === 1 ? "" : "s"}`);
  }
  if (parts.length === 0) return null;
  return (
    <div className="mt-2 -mb-0.5 text-right text-[10px] text-parsnip-muted/80 font-mono select-none">
      {parts.join(" · ")}
    </div>
  );
}

/**
 * Rendered inside <MessagePrimitive.If last>. Reads the live last-message
 * state from the Zustand store (tool calls, metadata) and renders our
 * streaming affordances alongside the actual message content.
 */
function AssistantLiveExtras() {
  const live = useLastAssistantMessage();
  const isStreaming = useThreadStore((s) => s.isStreaming);
  const meta = live?.meta;
  const hasTextContent = !!live?.content;

  return (
    <>
      {isStreaming && <StreamingIndicator hasContent={hasTextContent} />}
      {!isStreaming && meta && <MessageFooter meta={meta} />}
    </>
  );
}

function AssistantMessage() {
  return (
    <div className="flex justify-start mb-4">
      <div className="max-w-[85%] rounded-2xl rounded-bl-sm bg-navy-800 px-4 py-2.5 text-sm text-parsnip-text border border-navy-600">
        <MessagePrimitive.If last>
          {/* Tool badges render ABOVE the text so they appear the moment the
              first tool_call delta arrives, even before any content token. */}
          <ToolBadgesTop />
        </MessagePrimitive.If>

        <MessagePrimitive.Content
          components={{
            Text: AssistantMessageText,
          }}
        />

        <MessagePrimitive.If last>
          <AssistantLiveExtras />
        </MessagePrimitive.If>
      </div>
    </div>
  );
}

/** Just the badges, rendered above text content (for pre-token tool calls). */
function ToolBadgesTop() {
  const live = useLastAssistantMessage();
  const toolCalls = live?.toolCalls ?? [];
  if (toolCalls.length === 0) return null;
  return (
    <div className="mb-1.5 flex flex-wrap gap-1.5">
      {toolCalls.map((tc) => (
        <ToolCallBadge key={`${tc.index}-${tc.id}`} toolCall={tc} />
      ))}
    </div>
  );
}

const AssistantMessageText: TextMessagePartComponent = ({ text }) => {
  return <MarkdownRenderer content={text} />;
};
