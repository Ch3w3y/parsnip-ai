"use client";

import {
  AssistantRuntimeProvider,
  useExternalStoreRuntime,
  type ExternalStoreAdapter,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import { useState, useCallback, useEffect } from "react";
import {
  useThreadStore,
  OPTIMISTIC_THREAD_ID,
  type SerializedMessage,
} from "../stores/thread-store";

const ANALYSIS_TOOL_NAMES = new Set([
  "execute_python_script",
  "execute_r_script",
  "execute_notebook",
  "generate_dashboard",
]);

function hasRunningAnalysisTool(messages: SerializedMessage[]): boolean {
  const last = messages[messages.length - 1];
  if (last?.role !== "assistant" || !last.toolCalls?.length) return false;
  return last.toolCalls.some(
    (tc) => tc.status === "running" && ANALYSIS_TOOL_NAMES.has(tc.name),
  );
}

function convertMessage(msg: SerializedMessage): ThreadMessageLike {
  if (msg.role === "user") {
    return {
      role: "user",
      content: [{ type: "text" as const, text: msg.content }],
      createdAt: new Date(),
    };
  }
  return {
    role: "assistant",
    content: [{ type: "text" as const, text: msg.content }],
    createdAt: new Date(),
  };
}

function ParsnipRuntimeInner({ children }: { children: React.ReactNode }) {
  const currentThreadId = useThreadStore((s) => s.currentThreadId);
  const isStreaming = useThreadStore((s) => s.isStreaming);
  const setCurrentThreadId = useThreadStore((s) => s.setCurrentThreadId);
  const setStreaming = useThreadStore((s) => s.setStreaming);
  const loadThreads = useThreadStore((s) => s.loadThreads);
  const switchToThread = useThreadStore((s) => s.switchToThread);
  const switchToNewThread = useThreadStore((s) => s.switchToNewThread);

  const [runtimeMessages, setRuntimeMessages] = useState<SerializedMessage[]>([]);

  const onNew = useCallback(
    async (message: { content: any }) => {
      const text =
        message.content?.find((c: any) => c.type === "text")?.text || "";

      const store = useThreadStore.getState();

      const body: Record<string, unknown> = {
        model: "parsnip-agent",
        messages: [
          ...store.currentMessages,
          { role: "user", content: text },
        ],
        stream: true,
      };
      const existingThreadId = store.currentThreadId;
      if (existingThreadId) {
        body.thread_id = existingThreadId;
      }

      // Append user message, begin a placeholder assistant message, flip streaming on.
      store.appendMessage("user", text);
      store.beginAssistantMessage();
      setStreaming(true);

      // Optimistic sidebar row while we wait for the real thread id.
      if (!existingThreadId) {
        useThreadStore.getState().upsertThread({
          id: OPTIMISTIC_THREAD_ID,
          title: text.slice(0, 80) || "New thread",
          message_count: 1,
          created_at: new Date().toISOString(),
        });
      }

      const startedAt = Date.now();
      let threadListRefreshed = false;
      let toolCallCount = 0;
      let lastModelId: string | undefined;

      try {
        const response = await fetch("/api/chat/completions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });

        if (!response.ok) {
          const errorText = await response.text();
          throw new Error(`Agent error: ${response.status} ${errorText}`);
        }

        // Reconcile thread id as soon as the response headers land — this is
        // available well before the first token, unblocking the thread list.
        const newThreadId =
          response.headers.get("X-Thread-ID") || existingThreadId || "";
        if (newThreadId && newThreadId !== existingThreadId) {
          setCurrentThreadId(newThreadId);
          if (!existingThreadId) {
            useThreadStore.getState().removeOptimistic();
            useThreadStore.getState().upsertThread({
              id: newThreadId,
              title: text.slice(0, 80) || "Untitled",
              message_count: 2,
              created_at: new Date().toISOString(),
            });
            // Fire a background refresh; don't await — tokens keep flowing.
            loadThreads();
            threadListRefreshed = true;
          }
        }

        const reader = response.body?.getReader();
        if (!reader) throw new Error("No response body");

        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";

          for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed || !trimmed.startsWith("data: ")) continue;
            const data = trimmed.slice(6);
            if (data === "[DONE]") continue;

            let chunk: any;
            try {
              chunk = JSON.parse(data);
            } catch {
              continue;
            }

            if (chunk.model) {
              lastModelId = chunk.model;
            }

            const delta = chunk.choices?.[0]?.delta;
            if (!delta) continue;

            if (delta.content) {
              useThreadStore.getState().updateAssistantMessageText(delta.content);
            }

            if (Array.isArray(delta.tool_calls)) {
              for (const tc of delta.tool_calls) {
                const fn = tc.function ?? {};
                const storeState = useThreadStore.getState();
                const closingAnalysisTool = hasRunningAnalysisTool(
                  storeState.currentMessages,
                );
                useThreadStore.getState().appendToolCall({
                  id: tc.id ?? `call_${tc.index ?? 0}`,
                  index: typeof tc.index === "number" ? tc.index : 0,
                  name: fn.name ?? "tool",
                  args: typeof fn.arguments === "string" ? fn.arguments : "",
                });
                if (closingAnalysisTool) {
                  useThreadStore.getState().bumpAnalysisTool();
                }
                toolCallCount += 1;
              }
            }
          }
        }

        // Stream complete — close any still-running tool calls and stamp metadata.
        const closingAnalysisTool = hasRunningAnalysisTool(
          useThreadStore.getState().currentMessages,
        );
        useThreadStore.getState().markRunningToolCallsDone();
        if (closingAnalysisTool) {
          useThreadStore.getState().bumpAnalysisTool();
        }
        useThreadStore.getState().setLastMessageMeta({
          modelId: lastModelId,
          elapsedMs: Date.now() - startedAt,
          toolCount: toolCallCount,
        });
      } catch (err: any) {
        useThreadStore.getState().updateAssistantMessageText(
          `\n\n⚠️ ${err?.message || "Stream error"}`,
        );
        const closingAnalysisTool = hasRunningAnalysisTool(
          useThreadStore.getState().currentMessages,
        );
        useThreadStore.getState().markRunningToolCallsDone();
        if (closingAnalysisTool) {
          useThreadStore.getState().bumpAnalysisTool();
        }
      } finally {
        setStreaming(false);
        if (!threadListRefreshed) {
          loadThreads();
        }
      }
    },
    [setCurrentThreadId, setStreaming, loadThreads],
  );

  useEffect(() => {
    loadThreads();
  }, [loadThreads]);

  useEffect(() => {
    let prevMessages = useThreadStore.getState().currentMessages;
    const unsub = useThreadStore.subscribe((state) => {
      if (state.currentMessages !== prevMessages) {
        prevMessages = state.currentMessages;
        setRuntimeMessages(state.currentMessages);
      }
    });
    setRuntimeMessages(useThreadStore.getState().currentMessages);
    return unsub;
  }, []);

  const threadList = useThreadStore((s) => s.threads);

  const adapter: ExternalStoreAdapter<SerializedMessage> = {
    messages: runtimeMessages,
    convertMessage,
    onNew,
    isRunning: isStreaming,
    adapters: {
      threadList: {
        threads: threadList.map((t) => ({
          id: t.id,
          status: "regular" as const,
          title: t.title || "Untitled",
        })),
        onSwitchToThread: async (threadId: string) => {
          await switchToThread(threadId);
        },
        onSwitchToNewThread: () => {
          switchToNewThread();
        },
      },
    },
  };

  const runtime = useExternalStoreRuntime(adapter);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div className="aui-root flex flex-col h-screen bg-navy-950 text-parsnip-text">
        {children}
      </div>
    </AssistantRuntimeProvider>
  );
}

export function ParsnipRuntimeProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  return <ParsnipRuntimeInner>{children}</ParsnipRuntimeInner>;
}
