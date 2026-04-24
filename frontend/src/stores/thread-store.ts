import { create } from "zustand";

export interface ThreadInfo {
  id: string;
  title: string;
  message_count: number;
  created_at: string | null;
}

export interface ToolCall {
  id: string;
  index: number;
  name: string;
  args: string;
  status: "running" | "done" | "error";
  startedAt: number;
  endedAt?: number;
  output?: string;
  error?: string;
}

export interface MessageMeta {
  modelId?: string;
  elapsedMs?: number;
  toolCount?: number;
  promptTokens?: number;
  completionTokens?: number;
}

export interface SerializedMessage {
  role: "user" | "assistant" | "tool" | "unknown";
  content: string;
  name?: string;
  toolCalls?: ToolCall[];
  meta?: MessageMeta;
  /** Bumped per-chunk during streaming so memoized children re-render. */
  version?: number;
}

export const OPTIMISTIC_THREAD_ID = "__optimistic__";

interface ThreadState {
  threads: ThreadInfo[];
  currentThreadId: string | null;
  currentMessages: SerializedMessage[];
  messagesByThread: Record<string, SerializedMessage[]>;
  isLoading: boolean;
  error: string | null;
  isStreaming: boolean;
  isNewThread: boolean;
  lastAnalysisToolAt: number | null;

  loadThreads: () => Promise<void>;
  switchToThread: (threadId: string) => Promise<void>;
  switchToNewThread: () => void;
  setCurrentThreadId: (threadId: string | null) => void;
  setStreaming: (streaming: boolean) => void;
  bumpAnalysisTool: () => void;
  appendMessage: (role: string, content: string) => void;
  clearCurrentMessages: () => void;
  upsertThread: (thread: ThreadInfo) => void;

  beginAssistantMessage: () => void;
  updateAssistantMessageText: (delta: string) => void;
  appendToolCall: (tc: {
    id: string;
    index: number;
    name: string;
    args: string;
  }) => void;
  markRunningToolCallsDone: () => void;
  setLastMessageMeta: (meta: MessageMeta) => void;
  removeOptimistic: () => void;
}

async function responseError(prefix: string, res: Response): Promise<string> {
  try {
    const data = await res.json();
    const detail =
      typeof data?.detail === "string"
        ? data.detail
        : typeof data?.error === "string"
          ? data.error
          : typeof data?.message === "string"
            ? data.message
            : "";
    return detail ? `${prefix}: ${res.status} ${detail}` : `${prefix}: ${res.status}`;
  } catch {
    return `${prefix}: ${res.status}`;
  }
}

/** Immutably replace the last message in the array, preserving prefix identity. */
function replaceLast(
  messages: SerializedMessage[],
  updater: (last: SerializedMessage) => SerializedMessage,
): SerializedMessage[] {
  if (messages.length === 0) return messages;
  const last = messages[messages.length - 1];
  const nextLast = updater(last);
  if (nextLast === last) return messages;
  return [...messages.slice(0, -1), nextLast];
}

export const useThreadStore = create<ThreadState>((set, get) => ({
  threads: [],
  currentThreadId: null,
  currentMessages: [],
  messagesByThread: {},
  isLoading: false,
  error: null,
  isStreaming: false,
  isNewThread: true,
  lastAnalysisToolAt: null,

  loadThreads: async () => {
    set({ isLoading: true, error: null });
    try {
      const res = await fetch("/api/agent/threads?limit=50");
      if (res.ok) {
        const data = await res.json();
        const serverThreads: ThreadInfo[] = data.threads || [];
        // Preserve any still-pending optimistic row so we don't drop a brand-new
        // thread that the server hasn't committed yet.
        const { threads: existing } = get();
        const optimistic = existing.find((t) => t.id === OPTIMISTIC_THREAD_ID);
        const merged = optimistic
          ? [optimistic, ...serverThreads.filter((t) => t.id !== OPTIMISTIC_THREAD_ID)]
          : serverThreads;
        set({ threads: merged });
      } else {
        set({ error: await responseError("Failed to load threads", res) });
      }
    } catch {
      set({ error: "Network error loading threads" });
    } finally {
      set({ isLoading: false });
    }
  },

  switchToThread: async (threadId: string) => {
    const { messagesByThread } = get();
    const cached = messagesByThread[threadId];
    set({
      currentThreadId: threadId,
      currentMessages: cached ?? [],
      isNewThread: false,
      isLoading: true,
      error: null,
    });

    try {
      const res = await fetch(`/api/agent/threads/${threadId}`);
      if (res.ok) {
        const data = await res.json();
        const messages: SerializedMessage[] = data.messages || [];
        set((state) => ({
          currentMessages: messages,
          messagesByThread: { ...state.messagesByThread, [threadId]: messages },
        }));
      } else {
        set({ error: await responseError("Failed to load thread", res) });
      }
    } catch {
      set({ error: "Network error loading thread" });
    } finally {
      set({ isLoading: false });
    }
  },

  switchToNewThread: () => {
    const { currentThreadId, currentMessages, messagesByThread } = get();
    if (currentThreadId && currentMessages.length > 0) {
      set({
        messagesByThread: {
          ...messagesByThread,
          [currentThreadId]: currentMessages,
        },
      });
    }
    set({
      currentThreadId: null,
      currentMessages: [],
      isNewThread: true,
    });
  },

  setCurrentThreadId: (threadId: string | null) => {
    set({ currentThreadId: threadId, isNewThread: !threadId });
  },

  setStreaming: (streaming: boolean) => {
    set({ isStreaming: streaming });
  },

  bumpAnalysisTool: () => {
    set({ lastAnalysisToolAt: Date.now() });
  },

  appendMessage: (role: string, content: string) => {
    const { currentMessages, currentThreadId, messagesByThread } = get();
    const msg: SerializedMessage = {
      role: role as SerializedMessage["role"],
      content,
    };
    const updated = [...currentMessages, msg];
    set({ currentMessages: updated });
    if (currentThreadId) {
      set({
        messagesByThread: {
          ...messagesByThread,
          [currentThreadId]: updated,
        },
      });
    }
  },

  clearCurrentMessages: () => {
    set({ currentMessages: [] });
  },

  upsertThread: (thread: ThreadInfo) => {
    const { threads } = get();
    const idx = threads.findIndex((t) => t.id === thread.id);
    if (idx >= 0) {
      const updated = [...threads];
      updated[idx] = { ...updated[idx], ...thread };
      set({ threads: updated });
    } else {
      set({ threads: [thread, ...threads] });
    }
  },

  beginAssistantMessage: () => {
    const { currentMessages } = get();
    const msg: SerializedMessage = {
      role: "assistant",
      content: "",
      toolCalls: [],
      version: 0,
    };
    set({ currentMessages: [...currentMessages, msg] });
  },

  updateAssistantMessageText: (delta: string) => {
    const { currentMessages, currentThreadId, messagesByThread } = get();
    const updated = replaceLast(currentMessages, (last) => {
      if (last.role !== "assistant") return last;
      return {
        ...last,
        content: last.content + delta,
        version: (last.version ?? 0) + 1,
      };
    });
    if (updated === currentMessages) return;
    set({ currentMessages: updated });
    if (currentThreadId) {
      set({
        messagesByThread: {
          ...messagesByThread,
          [currentThreadId]: updated,
        },
      });
    }
  },

  appendToolCall: (tc) => {
    const { currentMessages, currentThreadId, messagesByThread } = get();
    const updated = replaceLast(currentMessages, (last) => {
      if (last.role !== "assistant") return last;
      const prior = last.toolCalls ?? [];
      // Mark any prior still-running tool calls as done — the backend doesn't
      // emit an explicit tool_end in the OpenAI-compat stream, so a new
      // tool_start implicitly closes the previous one.
      const now = Date.now();
      const closedPrior = prior.map((p) =>
        p.status === "running"
          ? { ...p, status: "done" as const, endedAt: now }
          : p,
      );
      const newTc: ToolCall = {
        id: tc.id,
        index: tc.index,
        name: tc.name,
        args: tc.args,
        status: "running",
        startedAt: now,
      };
      return {
        ...last,
        toolCalls: [...closedPrior, newTc],
        version: (last.version ?? 0) + 1,
      };
    });
    if (updated === currentMessages) return;
    set({ currentMessages: updated });
    if (currentThreadId) {
      set({
        messagesByThread: {
          ...messagesByThread,
          [currentThreadId]: updated,
        },
      });
    }
  },

  markRunningToolCallsDone: () => {
    const { currentMessages, currentThreadId, messagesByThread } = get();
    const updated = replaceLast(currentMessages, (last) => {
      if (last.role !== "assistant" || !last.toolCalls?.length) return last;
      const now = Date.now();
      const anyRunning = last.toolCalls.some((t) => t.status === "running");
      if (!anyRunning) return last;
      return {
        ...last,
        toolCalls: last.toolCalls.map((t) =>
          t.status === "running" ? { ...t, status: "done", endedAt: now } : t,
        ),
        version: (last.version ?? 0) + 1,
      };
    });
    if (updated === currentMessages) return;
    set({ currentMessages: updated });
    if (currentThreadId) {
      set({
        messagesByThread: {
          ...messagesByThread,
          [currentThreadId]: updated,
        },
      });
    }
  },

  setLastMessageMeta: (meta: MessageMeta) => {
    const { currentMessages, currentThreadId, messagesByThread } = get();
    const updated = replaceLast(currentMessages, (last) => {
      if (last.role !== "assistant") return last;
      return {
        ...last,
        meta: { ...(last.meta ?? {}), ...meta },
        version: (last.version ?? 0) + 1,
      };
    });
    if (updated === currentMessages) return;
    set({ currentMessages: updated });
    if (currentThreadId) {
      set({
        messagesByThread: {
          ...messagesByThread,
          [currentThreadId]: updated,
        },
      });
    }
  },

  removeOptimistic: () => {
    const { threads } = get();
    if (!threads.some((t) => t.id === OPTIMISTIC_THREAD_ID)) return;
    set({ threads: threads.filter((t) => t.id !== OPTIMISTIC_THREAD_ID) });
  },
}));

export const selectThreads = (state: ThreadState) => state.threads;
export const selectCurrentThreadId = (state: ThreadState) => state.currentThreadId;
export const selectIsLoading = (state: ThreadState) => state.isLoading;
export const selectError = (state: ThreadState) => state.error;
