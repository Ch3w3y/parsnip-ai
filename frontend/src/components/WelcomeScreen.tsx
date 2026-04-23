"use client";

import { ThreadPrimitive } from "@assistant-ui/react";

const SUGGESTIONS = [
  {
    label: "Research papers",
    prompt: "Research the latest machine learning papers from arxiv about reinforcement learning",
  },
  {
    label: "Search knowledge base",
    prompt: "Search my knowledge base for climate change data and recent findings",
  },
  {
    label: "Create a note",
    prompt: "Create a Joplin note summarizing our discussion with key takeaways",
  },
  {
    label: "Analyze data",
    prompt: "Analyze the latest forex rates and identify trends over the past week",
  },
];

export function WelcomeScreen() {
  return (
    <div className="flex flex-col items-center justify-center h-full px-6 py-16">
      {/* Brand mark */}
      <div className="mb-6">
        <div className="w-16 h-16 rounded-2xl bg-brand-gradient flex items-center justify-center shadow-lg shadow-parsnip-teal/20">
          <span className="text-white font-bold text-2xl">P</span>
        </div>
      </div>

      {/* Brand name */}
      <h1 className="text-4xl font-bold gradient-text mb-2">parsnip</h1>
      <p className="text-parsnip-muted text-sm mb-10">
        Grounded research &amp; analysis stack
      </p>

      {/* Suggestion chips using ThreadPrimitive.Suggestion */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-xl w-full">
        {SUGGESTIONS.map((s) => (
          <ThreadPrimitive.Suggestion
            key={s.label}
            prompt={s.prompt}
            className="text-left px-4 py-3 rounded-xl border border-navy-600 bg-navy-800/50 hover:bg-navy-700 hover:border-parsnip-teal/40 transition-all group"
            send
          >
            <div className="text-sm font-medium text-parsnip-text group-hover:text-parsnip-teal transition-colors">
              {s.label}
            </div>
            <div className="text-xs text-parsnip-muted mt-0.5 line-clamp-2">
              {s.prompt}
            </div>
          </ThreadPrimitive.Suggestion>
        ))}
      </div>
    </div>
  );
}