"use client";

/**
 * Tool UI registry — maps Parsnip agent tool names to React components.
 *
 * Each component is built with makeAssistantToolUI from @assistant-ui/react.
 * Tool names sharing the same visual style reuse a shared render function.
 *
 * In 0.12.x, makeAssistantToolUI returns React components that must be rendered
 * inside the AssistantRuntimeProvider tree.
 */

import { makeAssistantToolUI } from "@assistant-ui/react";
import { ApprovalUI } from "../ApprovalUI";

// ── Types ──────────────────────────────────────────────────────────────────

interface ToolArgs {
  [key: string]: unknown;
}

// ── Shared render functions ────────────────────────────────────────────────

function renderSearchTool({ args, status, result }: { args: ToolArgs; status: { type: string }; result: unknown }) {
  const query = (args.query as string) || (args.question as string) || "";
  const toolName = (args._tool as string) || "Search";

  if (status.type === "running") {
    return (
      <div className="tool-card flex items-center gap-3">
        <div className="w-7 h-7 rounded-md bg-parsnip-teal/15 flex items-center justify-center text-parsnip-teal text-sm">🔍</div>
        <div className="flex-1">
          <div className="text-xs text-parsnip-muted font-medium">{toolName}</div>
          <div className="shimmer h-4 w-48 rounded mt-1" />
        </div>
      </div>
    );
  }
  if (status.type === "error") {
    return (
      <div className="tool-card border-parsnip-error/40">
        <div className="flex items-center gap-2 text-parsnip-error text-sm"><span>⚠</span> Search failed</div>
        <p className="text-parsnip-muted text-xs mt-1">{String(result).slice(0, 200)}</p>
      </div>
    );
  }
  return (
    <div className="tool-card">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-parsnip-teal text-sm">🔍</span>
        <span className="text-xs font-semibold text-parsnip-muted uppercase tracking-wide">{toolName}</span>
        <span className="text-xs text-parsnip-muted px-1.5 py-0.5 rounded bg-navy-800">
          {query.length > 60 ? query.slice(0, 57) + "…" : query}
        </span>
      </div>
      <div className="text-sm text-parsnip-text whitespace-pre-wrap leading-relaxed">
        {typeof result === "string" ? result.slice(0, 800) + (result.length > 800 ? "…" : "") : JSON.stringify(result, null, 2)?.slice(0, 500)}
      </div>
    </div>
  );
}

function renderJoplinTool({ args, status, result }: { args: ToolArgs; status: { type: string }; result: unknown }) {
  const action = (args._tool as string) || "Joplin";

  if (status.type === "running") {
    return (
      <div className="tool-card flex items-center gap-3 border-l-2 border-l-indigo-500">
        <div className="w-7 h-7 rounded-md bg-indigo-500/15 flex items-center justify-center text-indigo-400 text-sm">📓</div>
        <div className="flex-1">
          <div className="text-xs text-indigo-400 font-medium">{action}</div>
          <div className="shimmer h-4 w-36 rounded mt-1" />
        </div>
      </div>
    );
  }
  if (status.type === "error") {
    return (
      <div className="tool-card border-l-2 border-l-parsnip-error">
        <span className="text-parsnip-error text-sm">⚠ Joplin error</span>
        <p className="text-parsnip-muted text-xs mt-1">{String(result).slice(0, 150)}</p>
      </div>
    );
  }
  const resultStr = typeof result === "string" ? result : JSON.stringify(result);
  const hasJoplinLink = resultStr?.includes("joplin://");
  const joplinId = hasJoplinLink ? resultStr?.match(/id=([a-f0-9]+)/)?.[1] : null;

  return (
    <div className="tool-card border-l-2 border-l-indigo-500">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-indigo-400 text-sm">📓</span>
        <span className="text-xs font-semibold text-indigo-400 uppercase tracking-wide">{action}</span>
      </div>
      <div className="text-sm text-parsnip-text whitespace-pre-wrap leading-relaxed">{resultStr?.slice(0, 600)}</div>
      {joplinId && (
        <a href={`joplin://x-callback-url/openNote?id=${joplinId}`} className="inline-flex items-center gap-1 mt-2 text-xs text-parsnip-teal hover:text-parsnip-blue transition-colors">
          Open in Joplin →
        </a>
      )}
    </div>
  );
}

function renderMemoryTool({ args, status, result }: { args: ToolArgs; status: { type: string }; result: unknown }) {
  const action = (args._tool as string) || "Memory";
  const category = (args.category as string) || "";
  const importance = args.importance as number | undefined;

  if (status.type === "running") {
    return (
      <div className="tool-card flex items-center gap-3 border-l-2 border-l-parsnip-teal">
        <div className="w-7 h-7 rounded-md bg-parsnip-teal/15 flex items-center justify-center text-parsnip-teal text-sm">🧠</div>
        <div className="flex-1">
          <div className="text-xs text-parsnip-teal font-medium">{action}</div>
          <div className="shimmer h-4 w-28 rounded mt-1" />
        </div>
      </div>
    );
  }
  if (status.type === "error") {
    return (
      <div className="tool-card border-l-2 border-l-parsnip-error">
        <span className="text-parsnip-error text-sm">⚠ Memory error</span>
      </div>
    );
  }
  const resultStr = typeof result === "string" ? result : JSON.stringify(result);
  return (
    <div className="tool-card border-l-2 border-l-parsnip-teal">
      <div className="flex items-center gap-2 mb-1">
        <span className="text-parsnip-teal text-sm">🧠</span>
        <span className="text-xs font-semibold text-parsnip-teal uppercase tracking-wide">{action}</span>
        {category && <span className="text-xs px-1.5 py-0.5 rounded bg-parsnip-teal/15 text-parsnip-teal">{category}</span>}
        {importance && (
          <span className="flex gap-0.5 ml-1">
            {Array.from({ length: 5 }).map((_, i) => (
              <span key={i} className={`w-1.5 h-1.5 rounded-full ${i < importance ? "bg-parsnip-teal" : "bg-navy-600"}`} />
            ))}
          </span>
        )}
      </div>
      <div className="text-sm text-parsnip-text whitespace-pre-wrap">{resultStr?.slice(0, 400)}</div>
    </div>
  );
}

function renderAnalysisTool({ args, status, result }: { args: ToolArgs; status: { type: string }; result: unknown }) {
  const action = (args._tool as string) || "Analysis";
  const code = (args.code as string) || (args.script as string) || "";

  if (status.type === "running") {
    return (
      <div className="tool-card">
        <div className="flex items-center gap-2 mb-2">
          <span className="text-parsnip-blue text-sm">▶</span>
          <span className="text-xs font-semibold text-parsnip-blue uppercase tracking-wide">{action}</span>
        </div>
        <div className="code-block text-parsnip-muted">
          <span className="inline-block w-2 h-4 bg-parsnip-teal animate-pulse" />
        </div>
      </div>
    );
  }
  if (status.type === "error") {
    return (
      <div className="tool-card border-parsnip-error/40">
        <div className="text-parsnip-error text-xs font-semibold mb-1">⚠ {action} failed</div>
        <div className="code-block text-parsnip-error overflow-x-auto text-xs">{String(result).slice(0, 500)}</div>
      </div>
    );
  }
  return (
    <div className="tool-card">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-parsnip-blue text-sm">▶</span>
        <span className="text-xs font-semibold text-parsnip-blue uppercase tracking-wide">{action}</span>
      </div>
      {code && (
        <details className="mb-2">
          <summary className="text-xs text-parsnip-muted cursor-pointer hover:text-parsnip-text">View code</summary>
          <div className="code-block mt-1 text-xs max-h-48 overflow-auto">{code.slice(0, 2000)}</div>
        </details>
      )}
      <div className="code-block text-sm whitespace-pre-wrap">
        {typeof result === "string" ? result.slice(0, 1500) : JSON.stringify(result, null, 2)?.slice(0, 500)}
      </div>
    </div>
  );
}

function renderWorkspaceTool({ args, status, result }: { args: ToolArgs; status: { type: string }; result: unknown }) {
  const action = (args._tool as string) || "Workspace";
  const isTerminal = action.includes("bash") || action.includes("execute_script");

  if (status.type === "running") {
    return (
      <div className="tool-card flex items-center gap-3">
        <div className="w-7 h-7 rounded-md bg-parsnip-warning/15 flex items-center justify-center text-parsnip-warning text-sm">
          {isTerminal ? "⌨" : "📁"}
        </div>
        <div className="flex-1">
          <div className="text-xs text-parsnip-warning font-medium">{action}</div>
          <div className="shimmer h-4 w-32 rounded mt-1" />
        </div>
      </div>
    );
  }
  if (status.type === "error") {
    return (
      <div className="tool-card border-parsnip-error/40">
        <span className="text-parsnip-error text-sm">⚠ {action} failed</span>
      </div>
    );
  }
  const resultStr = typeof result === "string" ? result : JSON.stringify(result);
  return (
    <div className="tool-card">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-parsnip-warning text-sm">{isTerminal ? "⌨" : "📁"}</span>
        <span className="text-xs font-semibold text-parsnip-warning uppercase tracking-wide">{action}</span>
      </div>
      <div className={`text-sm whitespace-pre-wrap ${isTerminal ? "code-block text-parsnip-text" : "text-parsnip-text"}`}>
        {resultStr?.slice(0, 1200)}
      </div>
    </div>
  );
}

function renderGitHubTool({ args, status, result }: { args: ToolArgs; status: { type: string }; result: unknown }) {
  const action = (args._tool as string) || "GitHub";
  const ghIcon = (
    <svg className="w-4 h-4 text-purple-400" viewBox="0 0 16 16" fill="currentColor">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.54.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/>
    </svg>
  );

  if (status.type === "running") {
    return (
      <div className="tool-card flex items-center gap-3 border-l-2 border-l-purple-500">
        {ghIcon}
        <div className="flex-1">
          <div className="text-xs text-purple-400 font-medium">{action}</div>
          <div className="shimmer h-4 w-40 rounded mt-1" />
        </div>
      </div>
    );
  }
  if (status.type === "error") {
    return (
      <div className="tool-card border-l-2 border-l-parsnip-error">
        <span className="text-parsnip-error text-sm">⚠ GitHub error</span>
      </div>
    );
  }
  const resultStr = typeof result === "string" ? result : JSON.stringify(result);
  return (
    <div className="tool-card border-l-2 border-l-purple-500">
      <div className="flex items-center gap-2 mb-2">
        {ghIcon}
        <span className="text-xs font-semibold text-purple-400 uppercase tracking-wide">{action}</span>
      </div>
      <div className="text-sm text-parsnip-text whitespace-pre-wrap leading-relaxed">{resultStr?.slice(0, 800)}</div>
    </div>
  );
}

function renderGenericTool({ args, status, result }: { args: ToolArgs; status: { type: string }; result: unknown }) {
  const action = (args._tool as string) || "Tool";

  if (status.type === "running") {
    return (
      <div className="tool-card flex items-center gap-3">
        <div className="w-7 h-7 rounded-md bg-navy-700 flex items-center justify-center text-parsnip-muted text-sm">⚙</div>
        <div className="flex-1">
          <div className="text-xs text-parsnip-muted font-medium">{action}</div>
          <div className="shimmer h-4 w-32 rounded mt-1" />
        </div>
      </div>
    );
  }
  if (status.type === "error") {
    return (
      <div className="tool-card border-parsnip-error/40">
        <span className="text-parsnip-error text-sm">⚠ {action} error</span>
      </div>
    );
  }
  return (
    <div className="tool-card">
      <div className="flex items-center gap-2 mb-1">
        <span className="text-parsnip-muted text-sm">⚙</span>
        <span className="text-xs font-semibold text-parsnip-muted uppercase tracking-wide">{action}</span>
      </div>
      <div className="text-sm text-parsnip-text whitespace-pre-wrap">
        {typeof result === "string" ? result.slice(0, 600) : JSON.stringify(result, null, 2)?.slice(0, 400)}
      </div>
    </div>
  );
}

// ── Register makeAssistantToolUI components ──────────────────────────────────
// In 0.12.x, makeAssistantToolUI returns a React component that must be rendered.
// We collect them here and render them in ToolUIRegistry.

// Search tools
const KBSearchTool = makeAssistantToolUI<ToolArgs, string>({ toolName: "kb_search", render: renderSearchTool });
const HolisticSearchTool = makeAssistantToolUI<ToolArgs, string>({ toolName: "holistic_search", render: renderSearchTool });
const AdaptiveSearchTool = makeAssistantToolUI<ToolArgs, string>({ toolName: "adaptive_search", render: renderSearchTool });
const ResearchTool = makeAssistantToolUI<ToolArgs, string>({ toolName: "research", render: renderSearchTool });
const FilteredSearchTool = makeAssistantToolUI<ToolArgs, string>({ toolName: "search_with_filters", render: renderSearchTool });
const FindSimilarTool = makeAssistantToolUI<ToolArgs, string>({ toolName: "find_similar", render: renderSearchTool });
const WebSearchTool = makeAssistantToolUI<ToolArgs, string>({ toolName: "web_search", render: renderSearchTool });
const ArxivSearchTool = makeAssistantToolUI<ToolArgs, string>({ toolName: "arxiv_search", render: renderSearchTool });

// Joplin tools
const JoplinSearchNotes = makeAssistantToolUI<ToolArgs, string>({ toolName: "joplin_search_notes", render: renderJoplinTool });
const JoplinGetNote = makeAssistantToolUI<ToolArgs, string>({ toolName: "joplin_get_note", render: renderJoplinTool });
const JoplinCreateNote = makeAssistantToolUI<ToolArgs, string>({ toolName: "joplin_create_note", render: renderJoplinTool });
const JoplinUpdateNote = makeAssistantToolUI<ToolArgs, string>({ toolName: "joplin_update_note", render: renderJoplinTool });
const JoplinEditNote = makeAssistantToolUI<ToolArgs, string>({ toolName: "joplin_edit_note", render: renderJoplinTool });
const JoplinDeleteNote = makeAssistantToolUI<ToolArgs, string>({ toolName: "joplin_delete_note", render: renderJoplinTool });
const JoplinListNotebooks = makeAssistantToolUI<ToolArgs, string>({ toolName: "joplin_list_notebooks", render: renderJoplinTool });
const JoplinCreateNotebook = makeAssistantToolUI<ToolArgs, string>({ toolName: "joplin_create_notebook", render: renderJoplinTool });
const JoplinListTags = makeAssistantToolUI<ToolArgs, string>({ toolName: "joplin_list_tags", render: renderJoplinTool });
const JoplinGetTagsForNote = makeAssistantToolUI<ToolArgs, string>({ toolName: "joplin_get_tags_for_note", render: renderJoplinTool });
const JoplinUploadResource = makeAssistantToolUI<ToolArgs, string>({ toolName: "joplin_upload_resource", render: renderJoplinTool });
const JoplinPing = makeAssistantToolUI<ToolArgs, string>({ toolName: "joplin_ping", render: renderJoplinTool });
const SaveNoteTool = makeAssistantToolUI<ToolArgs, string>({ toolName: "save_note", render: renderJoplinTool });
const ListDocumentsTool = makeAssistantToolUI<ToolArgs, string>({ toolName: "list_documents", render: renderJoplinTool });

// Memory tools
const SaveMemoryTool = makeAssistantToolUI<ToolArgs, string>({ toolName: "save_memory", render: renderMemoryTool });
const RecallMemoryTool = makeAssistantToolUI<ToolArgs, string>({ toolName: "recall_memory", render: renderMemoryTool });
const UpdateMemoryTool = makeAssistantToolUI<ToolArgs, string>({ toolName: "update_memory", render: renderMemoryTool });
const DeleteMemoryTool = makeAssistantToolUI<ToolArgs, string>({ toolName: "delete_memory", render: renderMemoryTool });
const RecallMemoryByCategory = makeAssistantToolUI<ToolArgs, string>({ toolName: "recall_memory_by_category", render: renderMemoryTool });
const SummarizeMemories = makeAssistantToolUI<ToolArgs, string>({ toolName: "summarize_memories", render: renderMemoryTool });

// Analysis tools
const ExecutePython = makeAssistantToolUI<ToolArgs, string>({ toolName: "execute_python_script", render: renderAnalysisTool });
const ExecuteR = makeAssistantToolUI<ToolArgs, string>({ toolName: "execute_r_script", render: renderAnalysisTool });
const ExecuteNotebook = makeAssistantToolUI<ToolArgs, string>({ toolName: "execute_notebook", render: renderAnalysisTool });
const GenerateDashboard = makeAssistantToolUI<ToolArgs, string>({ toolName: "generate_dashboard", render: renderAnalysisTool });
const ListAnalysisOutputs = makeAssistantToolUI<ToolArgs, string>({ toolName: "list_analysis_outputs", render: renderAnalysisTool });
const CreateScheduledJob = makeAssistantToolUI<ToolArgs, string>({ toolName: "create_scheduled_job", render: renderAnalysisTool });
const ListScheduledJobs = makeAssistantToolUI<ToolArgs, string>({ toolName: "list_scheduled_jobs", render: renderAnalysisTool });
const DeleteScheduledJob = makeAssistantToolUI<ToolArgs, string>({ toolName: "delete_scheduled_job", render: renderAnalysisTool });

// Workspace tools
const ListWorkspace = makeAssistantToolUI<ToolArgs, string>({ toolName: "list_workspace", render: renderWorkspaceTool });
const ReadWorkspaceFile = makeAssistantToolUI<ToolArgs, string>({ toolName: "read_workspace_file", render: renderWorkspaceTool });
const WriteWorkspaceFile = makeAssistantToolUI<ToolArgs, string>({ toolName: "write_workspace_file", render: renderWorkspaceTool });
const MakeWorkspaceDir = makeAssistantToolUI<ToolArgs, string>({ toolName: "make_workspace_dir", render: renderWorkspaceTool });
const DeleteWorkspaceItem = makeAssistantToolUI<ToolArgs, string>({ toolName: "delete_workspace_item", render: renderWorkspaceTool });
const MoveWorkspaceItem = makeAssistantToolUI<ToolArgs, string>({ toolName: "move_workspace_item", render: renderWorkspaceTool });
const ExecuteBashCommand = makeAssistantToolUI<ToolArgs, string>({ toolName: "execute_bash_command", render: renderWorkspaceTool });
const WriteAndExecuteScript = makeAssistantToolUI<ToolArgs, string>({ toolName: "write_and_execute_script", render: renderWorkspaceTool });
const ExecuteWorkspaceScript = makeAssistantToolUI<ToolArgs, string>({ toolName: "execute_workspace_script", render: renderWorkspaceTool });

// GitHub tools
const GitHubSearchRepos = makeAssistantToolUI<ToolArgs, string>({ toolName: "github_search_repos", render: renderGitHubTool });
const GitHubGetFile = makeAssistantToolUI<ToolArgs, string>({ toolName: "github_get_file", render: renderGitHubTool });
const GitHubListCommits = makeAssistantToolUI<ToolArgs, string>({ toolName: "github_list_commits", render: renderGitHubTool });
const GitHubSearchCode = makeAssistantToolUI<ToolArgs, string>({ toolName: "github_search_code", render: renderGitHubTool });
const GitHubListIssues = makeAssistantToolUI<ToolArgs, string>({ toolName: "github_list_issues", render: renderGitHubTool });
const GitHubCreateIssue = makeAssistantToolUI<ToolArgs, string>({ toolName: "github_create_issue", render: renderGitHubTool });
const GitHubListPullRequests = makeAssistantToolUI<ToolArgs, string>({ toolName: "github_list_pull_requests", render: renderGitHubTool });
const GitHubGetReadme = makeAssistantToolUI<ToolArgs, string>({ toolName: "github_get_readme", render: renderGitHubTool });
const GitHubGetRepoStructure = makeAssistantToolUI<ToolArgs, string>({ toolName: "github_get_repo_structure", render: renderGitHubTool });
const GitHubCreatePR = makeAssistantToolUI<ToolArgs, string>({ toolName: "github_create_pr", render: renderGitHubTool });
const GitHubListBranches = makeAssistantToolUI<ToolArgs, string>({ toolName: "github_list_branches", render: renderGitHubTool });

// Generic/other tools
const ExtractWebpage = makeAssistantToolUI<ToolArgs, string>({ toolName: "extract_webpage", render: renderGenericTool });
const Timeline = makeAssistantToolUI<ToolArgs, string>({ toolName: "timeline", render: renderGenericTool });
const KnowledgeGaps = makeAssistantToolUI<ToolArgs, string>({ toolName: "knowledge_gaps", render: renderGenericTool });
const CompareSources = makeAssistantToolUI<ToolArgs, string>({ toolName: "compare_sources", render: renderGenericTool });
const GetDocument = makeAssistantToolUI<ToolArgs, string>({ toolName: "get_document", render: renderGenericTool });
const IngestPdf = makeAssistantToolUI<ToolArgs, string>({ toolName: "ingest_pdf", render: renderGenericTool });
const GenerateKnowledgeGraph = makeAssistantToolUI<ToolArgs, string>({ toolName: "generate_knowledge_graph", render: renderGenericTool });
const SystemStatus = makeAssistantToolUI<ToolArgs, string>({ toolName: "system_status", render: renderGenericTool });

// ── Registry component — renders all tool UIs ────────────────────────────────

export function ToolUIRegistry() {
  return (
    <>
      {/* Search */}
      <KBSearchTool /><HolisticSearchTool /><AdaptiveSearchTool /><ResearchTool />
      <FilteredSearchTool /><FindSimilarTool /><WebSearchTool /><ArxivSearchTool />
      {/* Joplin */}
      <JoplinSearchNotes /><JoplinGetNote /><JoplinCreateNote /><JoplinUpdateNote />
      <JoplinEditNote /><JoplinDeleteNote /><JoplinListNotebooks /><JoplinCreateNotebook />
      <JoplinListTags /><JoplinGetTagsForNote /><JoplinUploadResource /><JoplinPing />
      <SaveNoteTool /><ListDocumentsTool />
      {/* Memory */}
      <SaveMemoryTool /><RecallMemoryTool /><UpdateMemoryTool /><DeleteMemoryTool />
      <RecallMemoryByCategory /><SummarizeMemories />
      {/* Analysis */}
      <ExecutePython /><ExecuteR /><ExecuteNotebook /><GenerateDashboard />
      <ListAnalysisOutputs /><CreateScheduledJob /><ListScheduledJobs /><DeleteScheduledJob />
      {/* Workspace */}
      <ListWorkspace /><ReadWorkspaceFile /><WriteWorkspaceFile /><MakeWorkspaceDir />
      <DeleteWorkspaceItem /><MoveWorkspaceItem /><ExecuteBashCommand />
      <WriteAndExecuteScript /><ExecuteWorkspaceScript />
      {/* GitHub */}
      <GitHubSearchRepos /><GitHubGetFile /><GitHubListCommits /><GitHubSearchCode />
      <GitHubListIssues /><GitHubCreateIssue /><GitHubListPullRequests /><GitHubGetReadme />
      <GitHubGetRepoStructure /><GitHubCreatePR /><GitHubListBranches />
      {/* Generic */}
      <ExtractWebpage /><Timeline /><KnowledgeGaps /><CompareSources />
      <GetDocument /><IngestPdf /><GenerateKnowledgeGraph /><SystemStatus />
    </>
  );
}