"use client";

import { useState } from "react";

interface AnalysisOutputProps {
  url: string;
  alt?: string;
}

function getOutputType(url: string): "image" | "svg" | "pdf" | "html" | "data" | "notebook" | "unknown" {
  const lower = url.toLowerCase();
  if (lower.endsWith(".png") || lower.endsWith(".jpg") || lower.endsWith(".jpeg") || lower.endsWith(".gif") || lower.endsWith(".webp")) return "image";
  if (lower.endsWith(".svg")) return "svg";
  if (lower.endsWith(".pdf")) return "pdf";
  if (lower.endsWith(".html")) return "html";
  if (lower.endsWith(".csv") || lower.endsWith(".json") || lower.endsWith(".tsv")) return "data";
  if (lower.endsWith(".ipynb")) return "notebook";
  return "unknown";
}

const FILE_ICONS: Record<string, string> = {
  image: "📊",
  svg: "📈",
  pdf: "📄",
  html: "🌐",
  data: "📋",
  notebook: "📓",
  unknown: "📎",
};

export function AnalysisOutput({ url, alt }: AnalysisOutputProps) {
  const [expanded, setExpanded] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(false);

  const outputType = getOutputType(url);
  const proxyUrl = url.startsWith("/") ? url : `/analysis/${url.replace(/^\/?/, "")}`;

  if (outputType === "image" || outputType === "svg") {
    return (
      <div className="my-2 rounded-lg border border-navy-600 overflow-hidden bg-navy-900">
        <button
          onClick={() => setExpanded(!expanded)}
          className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-parsnip-muted bg-navy-800 hover:bg-navy-700 transition-colors border-b border-navy-600"
        >
          <span>{FILE_ICONS[outputType]}</span>
          <span className="font-medium">{alt || "Analysis output"}</span>
          <span className="ml-auto text-parsnip-muted">{expanded ? "Collapse" : "Expand"}</span>
        </button>
        <div className={`${expanded ? "" : "max-h-64"} transition-all duration-200 overflow-hidden`}>
          {!error ? (
            <img
              src={proxyUrl}
              alt={alt || "Analysis output"}
              className={`w-full ${loaded ? "" : "shimmer h-48"}`}
              onLoad={() => setLoaded(true)}
              onError={() => setError(true)}
              loading="lazy"
            />
          ) : (
            <div className="p-4 text-center text-parsnip-muted text-sm">
              Failed to load image
            </div>
          )}
        </div>
      </div>
    );
  }

  if (outputType === "pdf") {
    return (
      <div className="my-2 rounded-lg border border-navy-600 overflow-hidden bg-navy-900">
        <a
          href={proxyUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-3 px-4 py-3 hover:bg-navy-700 transition-colors"
        >
          <div className="w-9 h-9 rounded-lg bg-parsnip-blue/15 flex items-center justify-center text-parsnip-blue">
            {FILE_ICONS.pdf}
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-sm font-medium text-parsnip-text truncate">{alt || "PDF Report"}</div>
            <div className="text-xs text-parsnip-muted">Click to open</div>
          </div>
          <svg className="w-4 h-4 text-parsnip-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" /></svg>
        </a>
      </div>
    );
  }

  if (outputType === "html") {
    return (
      <div className="my-2 rounded-lg border border-navy-600 overflow-hidden bg-navy-900">
        <a
          href={proxyUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-3 px-4 py-3 hover:bg-navy-700 transition-colors"
        >
          <div className="w-9 h-9 rounded-lg bg-parsnip-teal/15 flex items-center justify-center text-parsnip-teal">
            {FILE_ICONS.html}
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-sm font-medium text-parsnip-text truncate">{alt || "HTML Dashboard"}</div>
            <div className="text-xs text-parsnip-muted">Open in new tab</div>
          </div>
          <svg className="w-4 h-4 text-parsnip-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" /></svg>
        </a>
      </div>
    );
  }

  return (
    <div className="my-2 rounded-lg border border-navy-600 overflow-hidden bg-navy-900">
      <a
        href={proxyUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="flex items-center gap-3 px-4 py-3 hover:bg-navy-700 transition-colors"
      >
        <div className="w-9 h-9 rounded-lg bg-navy-700 flex items-center justify-center text-parsnip-muted">
          {FILE_ICONS[outputType]}
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-parsnip-text truncate">{alt || "Analysis output"}</div>
          <div className="text-xs text-parsnip-muted">{url.split("/").pop()}</div>
        </div>
        <svg className="w-4 h-4 text-parsnip-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" /></svg>
      </a>
    </div>
  );
}