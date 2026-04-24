"use client";

import type { ReactNode } from "react";

interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  description?: string;
  cta?: {
    label: string;
    onClick: () => void;
  };
}

export function EmptyState({
  icon,
  title,
  description,
  cta,
}: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-8 text-center">
      {icon ? <div className="mb-3 text-parsnip-muted">{icon}</div> : null}
      <div className="text-sm font-medium text-parsnip-text">{title}</div>
      {description ? (
        <div className="mt-1 max-w-sm text-xs leading-relaxed text-parsnip-muted">
          {description}
        </div>
      ) : null}
      {cta ? (
        <button
          type="button"
          onClick={cta.onClick}
          className="mt-4 rounded-md bg-parsnip-teal px-3 py-1.5 text-xs font-medium text-navy-950 transition-colors hover:brightness-110 focus:outline-none focus:ring-2 focus:ring-parsnip-teal/40"
        >
          {cta.label}
        </button>
      ) : null}
    </div>
  );
}
