import * as React from "react";

import { cn } from "@/lib/utils";

function Empty({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("flex flex-col items-center justify-center px-6 py-8 text-center", className)} {...props} />;
}
function EmptyHeader({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("flex max-w-sm flex-col items-center gap-1.5", className)} {...props} />;
}
function EmptyMedia({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("mb-2 text-muted-foreground", className)} {...props} />;
}
function EmptyTitle({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("text-sm font-medium text-foreground", className)} {...props} />;
}
function EmptyDescription({ className, ...props }: React.ComponentProps<"p">) {
  return <p className={cn("mt-1 max-w-sm text-xs leading-relaxed text-muted-foreground", className)} {...props} />;
}
function EmptyContent({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("mt-4 flex items-center justify-center", className)} {...props} />;
}

export { Empty, EmptyHeader, EmptyTitle, EmptyDescription, EmptyContent, EmptyMedia };
