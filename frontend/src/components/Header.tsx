"use client";

import { ReactNode } from "react";

export function Header() {
  return (
    <header className="flex items-center justify-between px-5 py-3 bg-navy-900 border-b border-navy-600 select-none">
      {/* Logo + brand */}
      <div className="flex items-center gap-3">
        {/* Mark: simplified "P" + dots from SVG */}
        <div className="w-8 h-8 rounded-lg bg-brand-gradient flex items-center justify-center">
          <span className="text-white font-bold text-sm">P</span>
        </div>
        <h1 className="text-lg font-bold gradient-text">parsnip</h1>
        <span className="text-parsnip-muted text-xs hidden sm:inline">
          Grounded research &amp; analysis
        </span>
      </div>

      {/* Status */}
      <div className="flex items-center gap-2 text-xs text-parsnip-muted">
        <span className="w-2 h-2 rounded-full bg-parsnip-teal pulse-dot" />
        Connected
      </div>
    </header>
  );
}