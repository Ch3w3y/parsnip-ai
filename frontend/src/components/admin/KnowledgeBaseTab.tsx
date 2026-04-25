"use client";

import { KBStatsPanel } from "@/components/KBStatsPanel";

export function KnowledgeBaseTab() {
  return (
    <div className="max-w-2xl">
      <h2 className="text-sm font-semibold text-parsnip-text mb-4">
        Knowledge Base
      </h2>
      <div className="rounded-md border border-navy-700 bg-navy-800 p-4">
        <KBStatsPanel />
      </div>
    </div>
  );
}