"use client";

import { ParsnipRuntimeProvider } from "./providers";
import { AppShell } from "../components/AppShell";
import { ToolUIRegistry } from "../components/tools/ToolUIs";

export default function Home() {
  return (
    <ParsnipRuntimeProvider>
      <ToolUIRegistry />
      <AppShell />
    </ParsnipRuntimeProvider>
  );
}