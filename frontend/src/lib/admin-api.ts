// Typed API client for the pi-agent admin endpoints.
// All calls go through the Next.js proxy at /api/admin/[...path].



export interface ServiceHealth {
  name: string;
  status: string;
  health: string;
  uptime_seconds: number | null;
}

export interface StackHealthResponse {
  services: ServiceHealth[];
}

export interface BackupEntry {
  name: string;
  size_bytes: number;
  created_at: string;
  type: string;
}

export interface ListBackupsResponse {
  backups: BackupEntry[];
}

export interface TriggerBackupResponse {
  job_id: string;
  status: string;
}

export interface RestorePreview {
  backup_id: string;
  type: string;
  size_bytes: number;
  created_at: string;
  would_restore_files: string[];
  estimated_duration_sec: number;
}

export interface ExecuteRestoreResponse {
  status: string;
  pid: number;
}



const ADMIN_BASE = "/api/admin";

async function request<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const url = `${ADMIN_BASE}${path}`;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options?.headers as Record<string, string> | undefined),
  };

  if (typeof window !== "undefined") {
    const token = sessionStorage.getItem("admin_token");
    if (token) {
      headers["X-Admin-Token"] = token;
    }
  }

  const res = await fetch(url, {
    ...options,
    headers,
  });

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`Admin API ${res.status}: ${body || res.statusText}`);
  }

  return res.json() as Promise<T>;
}

export async function getStackHealth(): Promise<StackHealthResponse> {
  return request<StackHealthResponse>("/stack/health");
}

export async function listBackups(
  type: string,
): Promise<ListBackupsResponse> {
  return request<ListBackupsResponse>(
    `/backups/list?type=${encodeURIComponent(type)}`,
  );
}

export async function triggerBackup(
  type: "kb" | "config" | "volumes",
): Promise<TriggerBackupResponse> {
  return request<TriggerBackupResponse>("/backups/trigger", {
    method: "POST",
    body: JSON.stringify({ type }),
  });
}

export async function getRestorePreview(
  backupId: string,
  type: string,
): Promise<RestorePreview> {
  return request<RestorePreview>(
    `/restore/preview?backup_id=${encodeURIComponent(backupId)}&type=${encodeURIComponent(type)}`,
  );
}

export async function executeRestore(
  backupId: string,
  type: string,
  confirmToken: string,
): Promise<ExecuteRestoreResponse> {
  return request<ExecuteRestoreResponse>("/restore/execute", {
    method: "POST",
    body: JSON.stringify({ backup_id: backupId, type, confirm_token: confirmToken }),
  });
}