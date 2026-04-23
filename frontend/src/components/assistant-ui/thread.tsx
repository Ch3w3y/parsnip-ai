"use client";

import {
  ThreadPrimitive,
  ComposerPrimitive,
  MessagePrimitive,
} from "@assistant-ui/react";
import { WelcomeScreen } from "../WelcomeScreen";

/**
 * Thread component built from assistant-ui 0.12.x primitives.
 * Since `Thread` is no longer exported from @assistant-ui/react,
 * we build it from ThreadPrimitive and ComposerPrimitive.
 */
export function Thread() {
  return (
    <ThreadPrimitive.Root className="flex h-full flex-col">
      {/* Empty state — shows welcome screen when no messages */}
      <ThreadPrimitive.Empty>
        <WelcomeScreen />
      </ThreadPrimitive.Empty>

      {/* Message viewport — shows when conversation has messages */}
      <ThreadPrimitive.Viewport className="flex-1 overflow-y-auto px-4 py-6 scroll-smooth">
        <ThreadPrimitive.Messages
          components={{
            UserMessage,
            AssistantMessage,
          }}
        />
      </ThreadPrimitive.Viewport>

      {/* Composer / input area */}
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

function UserMessageText({ ...props }: React.ComponentPropsWithoutRef<"div">) {
  return <div className="whitespace-pre-wrap" {...props} />;
}

function AssistantMessage() {
  return (
    <div className="flex justify-start mb-4">
      <div className="max-w-[85%] rounded-2xl rounded-bl-sm bg-navy-800 px-4 py-2.5 text-sm text-parsnip-text border border-navy-600">
        <MessagePrimitive.Content
          components={{
            Text: AssistantMessageText,
          }}
        />
        <MessagePrimitive.InProgress className="flex items-center gap-1.5 pt-2">
          <div className="w-1.5 h-1.5 rounded-full bg-parsnip-teal animate-pulse" />
          <div className="w-1.5 h-1.5 rounded-full bg-parsnip-teal animate-pulse delay-150" />
          <div className="w-1.5 h-1.5 rounded-full bg-parsnip-teal animate-pulse delay-300" />
        </MessagePrimitive.InProgress>
      </div>
    </div>
  );
}

function AssistantMessageText({ ...props }: React.ComponentPropsWithoutRef<"div">) {
  return <div className="whitespace-pre-wrap leading-relaxed" {...props} />;
}