# Cloud Storage & Deployment Architecture Plan

## 1. The "Live Volume" vs. Object Storage Dilemma
A common question when migrating from local Docker Compose to a cloud-native deployment is whether to mount cloud object storage (like AWS S3 or GCP Cloud Storage/GCS) directly as "live" container volumes (e.g., using `gcsfuse` or `s3fs`).

### **Why GCS is NOT for Live Databases**
You should **never** mount GCS or S3 as a live volume for `postgres` or `joplin-server`'s internal SQLite/Postgres databases.
*   **POSIX Non-Compliance:** Object stores do not support partial file overwrites, file locking, or `fsync` operations reliably.
*   **Latency & Corruption:** Databases require sub-millisecond block-storage performance. Running Postgres on a FUSE-mounted object store will lead to catastrophic database corruption and extreme latency.

### **The Correct Pattern: Block Storage + Object Backups**
The current architecture we built (using local volumes backed up to Parquet/Tarballs on GCS) is actually **the industry standard** for self-hosted containerized databases:
1.  **Live State:** Exists on fast, ephemeral, or highly-available block storage (e.g., Local SSDs, GCP Persistent Disks, AWS EBS, or a distributed block store like **Longhorn** or Ceph).
2.  **Disaster Recovery (DR):** Nightly/hourly scripts (like our `backup_kb.py` and `backup_config.py`) take a point-in-time snapshot of the database, compress it, and ship it to cold Object Storage (GCS).

---

## 2. Path Forward: The "Cloud Native" Configurable Deployment

To support both simple local deployments and highly-available cloud deployments without breaking the self-hosted ethos, we can implement a tiered storage architecture switch.

### **Tier 1: Single-Node Local (Current State)**
*   **Volumes:** Docker-managed local volumes (`pgdata`, `joplin_data`, `analysis_output`).
*   **Backup:** Cron jobs push snapshots to GCS (if `GCS_BUCKET` is provided).

### **Tier 2: Kubernetes / Swarm with Distributed Block Storage (Longhorn)**
*   If users deploy on a cluster, they can map the Docker volumes to **Longhorn StorageClasses**.
*   Longhorn provides synchronous replication across nodes. The application doesn't know it's highly available; it just sees a fast block device. The GCS backup scripts remain the secondary Disaster Recovery net.

### **Tier 3: The "Stateless Container" GCP/AWS Mode**
For users who want zero infrastructure maintenance, we can add a configuration switch (`DEPLOYMENT_MODE=cloud`) that modifies the stack:
1.  **Postgres:** The `docker-compose.yml` drops the local `postgres` container entirely, expecting `DATABASE_URL` to point to a managed service like **GCP Cloud SQL** or **AWS RDS**.
2.  **Analysis Outputs & Assets:** Instead of writing to a local `/app/output` volume, the analysis server and Joplin are configured to write directly to GCS/S3 using their respective API clients (bypassing the filesystem entirely).

## 3. Implementation Steps for the Cloud Switch

We can update `docker-compose.yml` to support profiles:

```yaml
# docker-compose.yml
services:
  agent:
    ...
  
  postgres:
    profiles: ["local"] # Only runs if deploying locally
    image: timescale/timescaledb-ha:pg16
    volumes:
      - pgdata:/home/postgres/pgdata/data
```

**Usage:**
*   Local Self-Hosters: `docker compose --profile local up -d`
*   Cloud Users: `docker compose up -d` (relies on external Cloud SQL and GCS APIs defined in `.env`).

## Conclusion
The backup archives we added (`scripts/backup_kb.py` and `backup_config.py`) are the **correct** and safest pattern for containerized application DR. Moving live database volumes to GCS would be a critical mistake. Instead, we will support production deployments via **Managed Block Storage (Longhorn)** or **Managed Database Services (Cloud SQL)**, while using GCS exclusively for cold backups and static asset storage.
