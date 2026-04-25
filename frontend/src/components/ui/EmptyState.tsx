"use client";

import type { ReactNode } from "react";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Button } from "@/components/ui/button";

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
    <Empty>
      <EmptyHeader>
        {icon ? <EmptyMedia>{icon}</EmptyMedia> : null}
        <EmptyTitle>{title}</EmptyTitle>
        {description ? <EmptyDescription>{description}</EmptyDescription> : null}
      </EmptyHeader>
      {cta ? (
        <EmptyContent>
          <Button
          type="button"
          onClick={cta.onClick}
          size="sm"
        >
          {cta.label}
          </Button>
        </EmptyContent>
      ) : null}
    </Empty>
  );
}
