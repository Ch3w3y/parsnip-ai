"use client";

import { useCallback, useEffect, useState } from "react";
import {
  listBackups,
  triggerBackup,
  getRestorePreview,
  executeRestore,
  type BackupEntry,
  type RestorePreview,
} from "@/lib/admin-api";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

type BackupType = "kb" | "config" | "volumes";
const BACKUP_TYPES: BackupType[] = ["kb", "config", "volumes"];
const BACKUP_LABELS: Record<BackupType, string> = {
  kb: "Knowledge Base",
  config: "Configuration",
  volumes: "Volumes",
};

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export function BackupsTab() {
  const [backups, setBackups] = useState<BackupEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [triggeringType, setTriggeringType] = useState<BackupType | null>(null);

  const [restoreDialogOpen, setRestoreDialogOpen] = useState(false);
  const [restorePreview, setRestorePreview] = useState<RestorePreview | null>(
    null,
  );
  const [restoreConfirmText, setRestoreConfirmText] = useState("");
  const [restoreExecuting, setRestoreExecuting] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const allBackups: BackupEntry[] = [];
      for (const t of BACKUP_TYPES) {
        const result = await listBackups(t);
        allBackups.push(...result.backups);
      }
      allBackups.sort(
        (a, b) =>
          new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      );
      setBackups(allBackups);
    } catch (err: any) {
      setError(err?.message || "Failed to fetch backups");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handleTrigger = async (type: BackupType) => {
    try {
      setTriggeringType(type);
      await triggerBackup(type);
      await refresh();
    } catch (err: any) {
      setError(err?.message || "Failed to trigger backup");
    } finally {
      setTriggeringType(null);
    }
  };

  const handleRestoreClick = async (backup: BackupEntry) => {
    try {
      const preview = await getRestorePreview(backup.name, backup.type);
      setRestorePreview(preview);
      setRestoreConfirmText("");
      setRestoreDialogOpen(true);
    } catch (err: any) {
      setError(err?.message || "Failed to get restore preview");
    }
  };

  const handleRestoreConfirm = async () => {
    if (!restorePreview) return;
    try {
      setRestoreExecuting(true);
      await executeRestore(
        restorePreview.backup_id,
        restorePreview.type,
        restorePreview.backup_id,
      );
      setRestoreDialogOpen(false);
      setRestorePreview(null);
      await refresh();
    } catch (err: any) {
      setError(err?.message || "Restore failed");
    } finally {
      setRestoreExecuting(false);
    }
  };

  if (loading && backups.length === 0) {
    return (
      <div className="flex items-center justify-center h-48">
        <Spinner className="h-6 w-6 text-parsnip-teal" />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-parsnip-text">Backups</h2>
        {error && (
          <p className="text-xs text-destructive">{error}</p>
        )}
      </div>

      <div className="flex gap-2">
        {BACKUP_TYPES.map((type) => (
          <Button
            key={type}
            variant="outline"
            size="sm"
            onClick={() => handleTrigger(type)}
            disabled={triggeringType !== null}
          >
            {triggeringType === type && (
              <Spinner className="mr-1.5 h-3 w-3" />
            )}
            Backup {BACKUP_LABELS[type]}
          </Button>
        ))}
      </div>

      {backups.length === 0 ? (
        <p className="text-sm text-parsnip-muted">No backups found.</p>
      ) : (
        <div className="rounded-md border border-navy-700">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent border-navy-700">
                <TableHead className="text-parsnip-muted">Type</TableHead>
                <TableHead className="text-parsnip-muted">Name</TableHead>
                <TableHead className="text-parsnip-muted">Created</TableHead>
                <TableHead className="text-parsnip-muted text-right">
                  Size
                </TableHead>
                <TableHead className="text-parsnip-muted text-right">
                  Actions
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {backups.map((b) => (
                <TableRow
                  key={b.name}
                  className="border-navy-700 hover:bg-navy-800/50"
                >
                  <TableCell>
                    <span className="text-xs font-medium text-parsnip-teal">
                      {BACKUP_LABELS[b.type as BackupType] ?? b.type}
                    </span>
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {b.name}
                  </TableCell>
                  <TableCell className="text-xs text-parsnip-muted">
                    {formatDate(b.created_at)}
                  </TableCell>
                  <TableCell className="text-xs text-parsnip-muted text-right">
                    {formatBytes(b.size_bytes)}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="outline"
                      size="xs"
                      onClick={() => handleRestoreClick(b)}
                    >
                      Restore
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <AlertDialog
        open={restoreDialogOpen}
        onOpenChange={(open) => {
          if (!open) {
            setRestoreDialogOpen(false);
            setRestorePreview(null);
            setRestoreConfirmText("");
          }
        }}
      >
        <AlertDialogContent className="bg-navy-800 border-navy-600">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-parsnip-text">
              Confirm Restore
            </AlertDialogTitle>
            <AlertDialogDescription className="text-parsnip-muted">
              {restorePreview ? (
                <span className="space-y-2">
                  <p>
                    This will restore{" "}
                    <strong className="text-parsnip-text">
                      {restorePreview.backup_id}
                    </strong>{" "}
                    ({restorePreview.type}).
                  </p>
                  <p>
                    Estimated duration:{" "}
                    {restorePreview.estimated_duration_sec}s
                  </p>
                  {restorePreview.would_restore_files.length > 0 && (
                    <details className="text-xs">
                      <summary className="cursor-pointer text-parsnip-teal">
                        {restorePreview.would_restore_files.length} files
                        affected
                      </summary>
                      <ul className="mt-1 space-y-0.5">
                        {restorePreview.would_restore_files.slice(0, 10).map((f) => (
                          <li key={f} className="font-mono">
                            {f}
                          </li>
                        ))}
                        {restorePreview.would_restore_files.length > 10 && (
                          <li className="text-parsnip-muted">
                            ...and{" "}
                            {restorePreview.would_restore_files.length - 10}{" "}
                            more
                          </li>
                        )}
                      </ul>
                    </details>
                  )}
                  <p className="mt-3 text-destructive font-medium">
                    Type the backup name to confirm:
                  </p>
                </span>
              ) : (
                "Loading preview..."
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="px-4">
            <input
              type="text"
              value={restoreConfirmText}
              onChange={(e) => setRestoreConfirmText(e.target.value)}
              placeholder={restorePreview?.backup_id ?? ""}
              className="w-full rounded-md border border-navy-600 bg-navy-900 px-3 py-2 text-sm text-parsnip-text placeholder:text-parsnip-muted/50 focus:outline-none focus:ring-1 focus:ring-parsnip-teal"
            />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-navy-600 text-parsnip-muted hover:text-parsnip-text">
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={handleRestoreConfirm}
              disabled={
                restoreConfirmText !== (restorePreview?.backup_id ?? "") ||
                restoreExecuting
              }
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90 disabled:opacity-50"
            >
              {restoreExecuting && <Spinner className="mr-1.5 h-3 w-3" />}
              Restore
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}