import * as React from "react";
import { Loader2Icon } from "lucide-react";

import { cn } from "@/lib/utils";

function Spinner({ className, ...props }: React.ComponentProps<typeof Loader2Icon>) {
  return <Loader2Icon role="status" aria-label="Loading" className={cn("h-4 w-4 animate-spin", className)} {...props} />;
}

export { Spinner };
