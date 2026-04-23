"use client";

import { ParsnipRuntimeProvider } from "./providers";
import { Header } from "../components/Header";
import { Thread } from "../components/assistant-ui/thread";
import { ToolBoundary } from "../components/tools/ToolBoundary";
import { ToolUIRegistry } from "../components/tools/ToolUIs";

export default function Home() {
  return (
    <ParsnipRuntimeProvider>
      <ToolUIRegistry />
      <Header />
      <div className="accent-line" />
      <main className="flex-1 overflow-hidden">
        <ToolBoundary>
          <Thread />
        </ToolBoundary>
      </main>
    </ParsnipRuntimeProvider>
  );
}