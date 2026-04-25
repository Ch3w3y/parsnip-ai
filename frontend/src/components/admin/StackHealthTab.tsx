"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getStackHealth,
  type ServiceHealth,
  type StackHealthResponse,
} from "@/lib/admin-api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/spinner";

function statusVariant(
  status: string,
): "default" | "secondary" | "destructive" | "outline" | "muted" {
  const s = status.toLowerCase();
  if (s === "running" || s === "healthy" || s === "ok" || s === "up")
    return "default";
  if (s === "degraded" || s === "warning" || s === "partial")
    return "secondary";
  if (s === "stopped" || s === "error" || s === "unhealthy" || s === "down")
    return "destructive";
  return "muted";
}

function healthVariant(
  health: string,
): "default" | "secondary" | "destructive" | "outline" | "muted" {
  const h = health.toLowerCase();
  if (h === "healthy" || h === "ok" || h === "passing") return "default";
  if (h === "degraded" || h === "warning") return "secondary";
  if (h === "unhealthy" || h === "failing" || h === "critical")
    return "destructive";
  return "muted";
}

function formatUptime(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 24) return `${Math.floor(h / 24)}d ${h % 24}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function ServiceCard({ service }: { service: ServiceHealth }) {
  return (
    <Card className="bg-navy-800 border-navy-700">
      <CardHeader className="pb-2">
        <CardTitle className="text-parsnip-text text-sm">
          {service.name}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="flex items-center gap-2">
          <span className="text-xs text-parsnip-muted w-12">Status</span>
          <Badge variant={statusVariant(service.status)}>
            {service.status}
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-parsnip-muted w-12">Health</span>
          <Badge variant={healthVariant(service.health)}>
            {service.health}
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-parsnip-muted w-12">Uptime</span>
          <span className="text-xs font-mono text-parsnip-text">
            {formatUptime(service.uptime_seconds)}
          </span>
        </div>
      </CardContent>
    </Card>
  );
}

export function StackHealthTab() {
  const [data, setData] = useState<StackHealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const result = await getStackHealth();
      setData(result);
    } catch (err: any) {
      setError(err?.message || "Failed to fetch stack health");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 10_000);
    return () => clearInterval(interval);
  }, [refresh]);

  if (loading && !data) {
    return (
      <div className="flex items-center justify-center h-48">
        <Spinner className="h-6 w-6 text-parsnip-teal" />
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="text-center py-12">
        <p className="text-destructive text-sm">{error}</p>
        <button
          onClick={refresh}
          className="mt-3 text-xs text-parsnip-teal hover:underline"
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-parsnip-text">
          Service Health
        </h2>
        <span className="text-[10px] text-parsnip-muted">
          Auto-refreshes every 10s
        </span>
      </div>

      {data && data.services.length > 0 ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
          {data.services.map((s) => (
            <ServiceCard key={s.name} service={s} />
          ))}
        </div>
      ) : (
        <p className="text-sm text-parsnip-muted">No services reported.</p>
      )}

      {error && (
        <p className="text-xs text-destructive">
          Last refresh failed: {error}
        </p>
      )}
    </div>
  );
}