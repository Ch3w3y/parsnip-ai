"use client";

import { useState, useEffect, useCallback } from "react";

interface ApprovalUIProps {
  /** What the agent wants to do (human-readable) */
  description: string;
  /** Called when user approves or auto-approve timer fires */
  onApprove: () => void;
  /** Called when user rejects */
  onReject: () => void;
  /** Seconds before auto-approve (0 = no auto-approve) */
  autoApproveSeconds?: number;
}

export function ApprovalUI({
  description,
  onApprove,
  onReject,
  autoApproveSeconds = 30,
}: ApprovalUIProps) {
  const [remaining, setRemaining] = useState(autoApproveSeconds);
  const [decided, setDecided] = useState(false);

  useEffect(() => {
    if (autoApproveSeconds <= 0) return;
    const interval = setInterval(() => {
      setRemaining((prev) => {
        if (prev <= 1) {
          clearInterval(interval);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, [autoApproveSeconds]);

  useEffect(() => {
    if (remaining === 0 && !decided) {
      setDecided(true);
      onApprove();
    }
  }, [remaining, decided, onApprove]);

  const handleApprove = useCallback(() => {
    setDecided(true);
    onApprove();
  }, [onApprove]);

  const handleReject = useCallback(() => {
    setDecided(true);
    onReject();
  }, [onReject]);

  if (decided) return null;

  const progress = autoApproveSeconds > 0 ? remaining / autoApproveSeconds : 1;
  const circumference = 2 * Math.PI * 14;
  const dashOffset = circumference * (1 - progress);

  return (
    <div className="tool-card border-parsnip-warning/60 bg-navy-800/50">
      <div className="flex items-start gap-3">
        {/* Countdown ring */}
        {autoApproveSeconds > 0 && (
          <div className="flex-shrink-0 mt-0.5">
            <svg width="32" height="32" className="countdown-ring">
              <circle
                cx="16"
                cy="16"
                r="14"
                fill="none"
                stroke="#2d3b4f"
                strokeWidth="2"
              />
              <circle
                cx="16"
                cy="16"
                r="14"
                fill="none"
                stroke="#23c0a8"
                strokeWidth="2"
                strokeLinecap="round"
                strokeDasharray={circumference}
                strokeDashoffset={dashOffset}
                style={{ transition: "stroke-dashoffset 1s linear" }}
              />
            </svg>
            <span className="absolute text-[10px] text-parsnip-teal font-mono -translate-x-1/2 -translate-y-1/2 top-1/2 left-1/2">
              {remaining}
            </span>
          </div>
        )}

        <div className="flex-1">
          <div className="text-xs font-semibold text-parsnip-warning flex items-center gap-1.5 mb-1">
            ⚡ Approval Required
          </div>
          <p className="text-sm text-parsnip-text mb-3">{description}</p>

          <div className="flex gap-2">
            <button
              onClick={handleApprove}
              className="px-4 py-1.5 text-sm font-medium rounded-md bg-parsnip-teal/15 text-parsnip-teal hover:bg-parsnip-teal/25 transition-colors border border-parsnip-teal/30"
            >
              Approve
            </button>
            <button
              onClick={handleReject}
              className="px-4 py-1.5 text-sm font-medium rounded-md bg-parsnip-error/15 text-parsnip-error hover:bg-parsnip-error/25 transition-colors border border-parsnip-error/30"
            >
              Reject
            </button>
          </div>

          {autoApproveSeconds > 0 && (
            <p className="text-[10px] text-parsnip-muted mt-2">
              Auto-approving in {remaining}s
            </p>
          )}
        </div>
      </div>
    </div>
  );
}