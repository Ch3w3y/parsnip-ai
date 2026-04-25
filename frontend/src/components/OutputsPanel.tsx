"use client";

import React from "react";
import { useThreadStore, selectCurrentThreadId } from "../stores/thread-store";
import { EmptyState } from "./ui/EmptyState";
import { ErrorBanner } from "./ui/ErrorBanner";
import { LoadingSkeleton } from "./ui/LoadingSkeleton";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { PanelActions, PanelHeader, PanelIconButton, PanelTitle } from "./ui/panel";

interface OutputFile {
  name: string;
  type?: string;
  size?: number;
  modified?: string;
}

const FILE_TYPES = ["png", "svg", "pdf", "html", "csv"] as const;
type FileType = (typeof FILE_TYPES)[number];

function getExtension(name: string): string {
  const parts = name.split(".");
  if (parts.length < 2) return "";
  return parts[parts.length - 1].toLowerCase();
}

function PdfIcon() {
  return (
    <svg viewBox="0 0 40 40" className="w-full h-full" fill="none">
      <rect x="6" y="4" width="28" height="32" rx="3" stroke="currentColor" strokeWidth="1.5" className="text-parsnip-teal" />
      <text x="20" y="23" textAnchor="middle" fill="currentColor" className="text-parsnip-teal" fontSize="9" fontWeight="600">PDF</text>
    </svg>
  );
}

function HtmlIcon() {
  return (
    <svg viewBox="0 0 40 40" className="w-full h-full" fill="none">
      <text x="20" y="24" textAnchor="middle" fill="currentColor" className="text-parsnip-teal" fontSize="13" fontWeight="600" fontFamily="monospace">&lt;/&gt;</text>
    </svg>
  );
}

function DataIcon() {
  return (
    <svg viewBox="0 0 40 40" className="w-full h-full text-parsnip-teal" fill="none" stroke="currentColor" strokeWidth="1.5">
      <rect x="8" y="8" width="24" height="24" rx="2" />
      <line x1="8" y1="16" x2="32" y2="16" />
      <line x1="8" y1="24" x2="32" y2="24" />
      <line x1="18" y1="8" x2="18" y2="32" />
    </svg>
  );
}

function RefreshIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
      <polyline points="21,3 21,9 15,9" />
    </svg>
  );
}

export function OutputsPanel() {
  const lastAnalysisToolAt = useThreadStore((s) => s.lastAnalysisToolAt);
  const currentThreadId = useThreadStore(selectCurrentThreadId);
  const threads = useThreadStore((s) => s.threads);
  const [outputs, setOutputs] = React.useState<OutputFile[]>([]);
  const [isLoading, setIsLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [typeFilter, setTypeFilter] = React.useState<string>("");

  const currentThreadTitle = currentThreadId
    ? threads.find((t) => t.id === currentThreadId)?.title ?? "Thread"
    : null;

  const fetchOutputs = React.useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await fetch("/analysis/outputs");
      if (!res.ok) {
        throw new Error(`Failed to fetch outputs (${res.status})`);
      }
      const data = await res.json();

      let files: OutputFile[] = [];
      if (Array.isArray(data)) {
        files = data;
      } else if (data && typeof data === "object") {
        if (Array.isArray(data.files)) {
          files = data.files;
        } else if (Array.isArray(data.outputs)) {
          files = data.outputs;
        }
      }

      if (!Array.isArray(files)) {
        files = [];
      }

      setOutputs(
        files
          .map((f: OutputFile & { path?: string }) => ({
            ...f,
            name: typeof f.name === "string" ? f.name : f.path ?? "",
          }))
          .filter((f) => f && typeof f.name === "string" && f.name.length > 0),
      );
    } catch (err) {
      if (err instanceof SyntaxError) {
        setError("Unable to load outputs");
      } else {
        setError(err instanceof Error ? err.message : "Unable to load outputs");
      }
      setOutputs([]);
    } finally {
      setIsLoading(false);
    }
  }, []);

  React.useEffect(() => {
    fetchOutputs();
  }, [currentThreadId, fetchOutputs]);

  React.useEffect(() => {
    if (lastAnalysisToolAt !== null) {
      fetchOutputs();
    }
  }, [lastAnalysisToolAt, fetchOutputs]);

  const filteredOutputs = React.useMemo(() => {
    if (!typeFilter) return outputs;
    return outputs.filter((f) => {
      const ext = getExtension(f.name);
      return ext === typeFilter;
    });
  }, [outputs, typeFilter]);

  const activeTypes = React.useMemo(() => {
    const exts = new Set(outputs.map((f) => getExtension(f.name)));
    return FILE_TYPES.filter((t) => exts.has(t));
  }, [outputs]);

  const fileUrl = (name: string) => `/analysis/outputs/${encodeURIComponent(name)}`;

  const renderThumbnail = (file: OutputFile) => {
    const ext = getExtension(file.name);
    if (ext === "png" || ext === "svg") {
      return (
        <img
          src={fileUrl(file.name)}
          alt={file.name}
          className="w-full h-full object-cover rounded border border-navy-700"
          loading="lazy"
        />
      );
    }
    if (ext === "pdf") {
      return (
        <div className="w-full h-full flex items-center justify-center bg-navy-800 rounded border border-navy-700">
          <PdfIcon />
        </div>
      );
    }
    if (ext === "html") {
      return (
        <div className="w-full h-full flex items-center justify-center bg-navy-800 rounded border border-navy-700">
          <HtmlIcon />
        </div>
      );
    }
    return (
      <div className="w-full h-full flex items-center justify-center bg-navy-800 rounded border border-navy-700">
        <DataIcon />
      </div>
    );
  };

  const formatSize = (bytes?: number) => {
    if (bytes == null) return "";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <PanelHeader>
        <PanelTitle>Outputs</PanelTitle>
        <PanelActions>
          <PanelIconButton
          onClick={fetchOutputs}
          disabled={isLoading}
          label="Refresh outputs"
        >
          <RefreshIcon />
          </PanelIconButton>
        </PanelActions>
      </PanelHeader>

      <div className="px-3 py-1.5 border-b border-navy-800 flex items-center gap-1.5">
        <Badge variant={currentThreadId ? "default" : "muted"} className="max-w-full truncate text-[10px]">
          {currentThreadTitle
            ? `Thread: ${currentThreadTitle}`
            : "All threads"}
        </Badge>
      </div>

      {outputs.length > 0 && (
        <div className="flex items-center gap-1 px-3 py-2 border-b border-navy-800 flex-wrap">
          <Button
            onClick={() => setTypeFilter("")}
            variant={typeFilter === "" ? "success" : "outline"}
            size="xs"
            className="h-6 text-[10px]"
          >
            All
          </Button>
          {activeTypes.map((ft) => (
            <Button
              key={ft}
              onClick={() => setTypeFilter(ft === typeFilter ? "" : ft)}
              variant={typeFilter === ft ? "success" : "outline"}
              size="xs"
              className="h-6 text-[10px] uppercase"
            >
              {ft}
            </Button>
          ))}
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-3 py-3">
        {isLoading && (
          <LoadingSkeleton variant="list" rows={4} />
        )}

        {!isLoading && error && (
          <ErrorBanner
            message={error}
            detail="/analysis/outputs"
            onRetry={fetchOutputs}
          />
        )}

        {!isLoading && !error && outputs.length === 0 && (
          <EmptyState
            title="No analysis outputs yet"
            description="Run an analysis tool to generate charts, dashboards, or files."
          />
        )}

        {!isLoading && !error && outputs.length > 0 && filteredOutputs.length === 0 && (
          <EmptyState
            title="No outputs match this filter"
            description="Try another file type or clear the filter."
            cta={{ label: "Show all", onClick: () => setTypeFilter("") }}
          />
        )}

        {filteredOutputs.length > 0 && (
          <div className="grid grid-cols-2 gap-2">
            {filteredOutputs.map((file) => (
              <a
                key={file.name}
                href={fileUrl(file.name)}
                target="_blank"
                rel="noopener noreferrer"
                className="group flex flex-col gap-1 hover:opacity-80 transition-opacity"
                title={`${file.name}${file.size ? ` — ${formatSize(file.size)}` : ""}`}
              >
                <div className="aspect-square max-h-24 rounded overflow-hidden">
                  {renderThumbnail(file)}
                </div>
                <span className="text-[10px] text-parsnip-muted truncate">
                  {file.name}
                </span>
              </a>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
