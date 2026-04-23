"use client";

import { AssistantRuntimeProvider, useLocalRuntime, type ChatModelAdapter } from "@assistant-ui/react";

/**
 * Parsnip agent adapter — streams from /v1/chat/completions via SSE.
 *
 * Uses OpenAI-compatible streaming format. The agent at localhost:8000
 * returns SSE events in OpenAI chat completion chunk format:
 *   data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","choices":[{"delta":{"role":"assistant","content":"..."}}]}
 *   data: [DONE]
 */
const ParsnipAdapter: ChatModelAdapter = {
  async *run({ messages, abortSignal }) {
    // Convert assistant-ui messages to OpenAI format
    const openaiMessages = messages
      .filter((m) => m.role === "user" || m.role === "assistant")
      .map((m) => {
        const text = m.content
          .filter((c): c is { type: "text"; text: string } => c.type === "text")
          .map((c) => c.text)
          .join("\n");
        return { role: m.role, content: text };
      });

    const response = await fetch("/api/chat/completions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model: "parsnip-agent",
        messages: openaiMessages,
        stream: true,
      }),
      signal: abortSignal,
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Agent error: ${response.status} ${errorText}`);
    }

    const reader = response.body?.getReader();
    if (!reader) throw new Error("No response body");

    const decoder = new TextDecoder();
    let buffer = "";
    let fullText = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // Parse SSE lines
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? ""; // Keep incomplete line in buffer

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || !trimmed.startsWith("data: ")) continue;

        const data = trimmed.slice(6); // Remove "data: "
        if (data === "[DONE]") continue;

        try {
          const chunk = JSON.parse(data);
          const delta = chunk.choices?.[0]?.delta;
          if (delta?.content) {
            fullText += delta.content;
            yield {
              content: [{ type: "text" as const, text: fullText }],
            };
          }
        } catch {
          // Skip unparseable chunks
        }
      }
    }

    // Yield final content if any remaining text
    if (fullText) {
      yield {
        content: [{ type: "text" as const, text: fullText }],
      };
    }
  },
};

export function ParsnipRuntimeProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const runtime = useLocalRuntime(ParsnipAdapter);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div className="aui-root flex flex-col h-screen bg-navy-950 text-parsnip-text">
        {children}
      </div>
    </AssistantRuntimeProvider>
  );
}