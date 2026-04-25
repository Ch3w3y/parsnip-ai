"use client";

import Link from "next/link";
import { useState } from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { StackHealthTab } from "./StackHealthTab";
import { BackupsTab } from "./BackupsTab";
import { KnowledgeBaseTab } from "./KnowledgeBaseTab";

function ArrowLeftIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M19 12H5" />
      <polyline points="12,19 5,12 12,5" />
    </svg>
  );
}

export function AdminShell() {
  const [activeTab, setActiveTab] = useState("health");

  return (
    <div className="flex flex-col h-screen bg-navy-950 text-parsnip-text">
      <header className="flex items-center gap-4 px-5 py-3 border-b border-navy-600 bg-navy-900">
        <Link
          href="/"
          className="flex items-center gap-1.5 text-sm text-parsnip-muted hover:text-parsnip-text transition-colors"
        >
          <ArrowLeftIcon />
          Home
        </Link>

        <div className="w-px h-5 bg-navy-600" />

        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-brand-gradient flex items-center justify-center">
            <span className="text-white font-bold text-xs">A</span>
          </div>
          <h1 className="text-lg font-bold gradient-text">Admin Console</h1>
        </div>

        <div className="flex-1" />

        <span className="text-xs text-parsnip-muted">pi-agent</span>
      </header>

      <div className="accent-line" />

      <div className="flex-1 overflow-hidden">
        <Tabs
          value={activeTab}
          onValueChange={setActiveTab}
          className="flex flex-col h-full"
        >
          <div className="px-5 pt-4 border-b border-navy-700">
            <TabsList className="bg-navy-800">
              <TabsTrigger value="health">Stack Health</TabsTrigger>
              <TabsTrigger value="kb">Knowledge Base</TabsTrigger>
              <TabsTrigger value="backups">Backups</TabsTrigger>
            </TabsList>
          </div>

          <TabsContent value="health" className="flex-1 overflow-auto p-5">
            <StackHealthTab />
          </TabsContent>

          <TabsContent value="kb" className="flex-1 overflow-auto p-5">
            <KnowledgeBaseTab />
          </TabsContent>

          <TabsContent value="backups" className="flex-1 overflow-auto p-5">
            <BackupsTab />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}