"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import rehypeRaw from "rehype-raw";
import type { Components } from "react-markdown";

function proxyUrl(src: string | undefined): string | undefined {
  if (!src) return src;
  if (src.startsWith("/outputs/") || src.startsWith("outputs/")) {
    return `/analysis/${src.replace(/^\/?outputs\//, "outputs/")}`;
  }
  if (src.includes(":8095/outputs/")) {
    return src.replace(/https?:\/\/[^/]+:8095\//, "/analysis/");
  }
  return src;
}

const markdownComponents: Components = {
  img: ({ src, alt, ...props }) => {
    const resolvedSrc = proxyUrl(src);

    return (
      <img
        src={resolvedSrc}
        alt={alt || ""}
        className="max-w-full h-auto rounded-md my-2"
        loading="lazy"
        {...props}
      />
    );
  },
  a: ({ href, children, ...props }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-parsnip-teal hover:text-parsnip-blue underline underline-offset-2 transition-colors"
      {...props}
    >
      {children}
    </a>
  ),
  code: ({ className, children, ...props }) => {
    const isInline = !className;
    if (isInline) {
      return (
        <code
          className="bg-navy-700 text-parsnip-teal px-1.5 py-0.5 rounded text-xs font-mono"
          {...props}
        >
          {children}
        </code>
      );
    }
    const lang = className?.replace("language-", "") || "";
    return (
      <div className="my-2 rounded-lg border border-navy-600 overflow-hidden">
        {lang && (
          <div className="px-3 py-1 text-xs text-parsnip-muted bg-navy-800 border-b border-navy-600 font-mono">
            {lang}
          </div>
        )}
        <pre className="p-3 overflow-x-auto bg-navy-900 text-parsnip-text text-xs leading-relaxed">
          <code className={className} {...props}>{children}</code>
        </pre>
      </div>
    );
  },
  table: ({ children, ...props }) => (
    <div className="my-2 overflow-x-auto rounded-lg border border-navy-600">
      <table className="min-w-full text-xs" {...props}>
        {children}
      </table>
    </div>
  ),
  thead: ({ children, ...props }) => (
    <thead className="bg-navy-800 text-parsnip-muted" {...props}>{children}</thead>
  ),
  tbody: ({ children, ...props }) => (
    <tbody className="divide-y divide-navy-600" {...props}>{children}</tbody>
  ),
  tr: ({ children, ...props }) => (
    <tr className="border-b border-navy-600 last:border-0" {...props}>{children}</tr>
  ),
  th: ({ children, ...props }) => (
    <th className="px-3 py-2 text-left font-medium text-parsnip-muted" {...props}>{children}</th>
  ),
  td: ({ children, ...props }) => (
    <td className="px-3 py-2 text-parsnip-text" {...props}>{children}</td>
  ),
  blockquote: ({ children, ...props }) => (
    <blockquote
      className="border-l-3 border-parsnip-teal pl-4 my-2 text-parsnip-muted text-sm italic"
      {...props}
    >
      {children}
    </blockquote>
  ),
  h1: ({ children, ...props }) => (
    <h1 className="text-xl font-bold text-parsnip-text mt-4 mb-2" {...props}>{children}</h1>
  ),
  h2: ({ children, ...props }) => (
    <h2 className="text-lg font-bold text-parsnip-text mt-3 mb-1.5" {...props}>{children}</h2>
  ),
  h3: ({ children, ...props }) => (
    <h3 className="text-base font-semibold text-parsnip-text mt-2 mb-1" {...props}>{children}</h3>
  ),
  p: ({ children, ...props }) => (
    <p className="my-1.5 leading-relaxed" {...props}>{children}</p>
  ),
  ul: ({ children, ...props }) => (
    <ul className="my-1.5 ml-4 list-disc space-y-0.5 text-sm" {...props}>{children}</ul>
  ),
  ol: ({ children, ...props }) => (
    <ol className="my-1.5 ml-4 list-decimal space-y-0.5 text-sm" {...props}>{children}</ol>
  ),
  li: ({ children, ...props }) => (
    <li className="text-parsnip-text leading-relaxed" {...props}>{children}</li>
  ),
  hr: () => <hr className="border-navy-600 my-3" />,
  details: ({ children, ...props }) => (
    <details className="my-2 rounded-lg border border-navy-600 bg-navy-800" {...props}>
      {children}
    </details>
  ),
  summary: ({ children, ...props }) => (
    <summary className="px-3 py-2 text-xs text-parsnip-muted cursor-pointer hover:text-parsnip-text transition-colors select-none" {...props}>
      {children}
    </summary>
  ),
};

interface MarkdownRendererProps {
  content: string;
  className?: string;
}

export function MarkdownRenderer({ content, className }: MarkdownRendererProps) {
  return (
    <div className={`markdown-content ${className ?? ""}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeKatex, { strict: false }], rehypeRaw]}
        components={markdownComponents}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}