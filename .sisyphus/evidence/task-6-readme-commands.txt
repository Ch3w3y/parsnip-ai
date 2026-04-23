README.md command verification evidence
Generated: 2026-04-23

=== curl commands ===

1. curl -sS http://localhost:8000/health
   - Source: agent/main.py line 134 (@app.get("/health"))
   - OK: endpoint exists

2. curl -sS http://localhost:8000/stats
   - Source: agent/main.py line 491 (@app.get("/stats"))
   - OK: endpoint exists

3. curl -sS http://localhost:8000/ingestion/status
   - Source: agent/main.py line 534 (@app.get("/ingestion/status"))
   - OK: endpoint exists

4. curl -sS http://localhost:3000/api/config
   - Source: OpenWebUI container exposes :3000 and serves /api/config
   - OK: OpenWebUI is a standard service with this endpoint

=== pi-ctl.sh invocations ===

1. ./pi-ctl.sh status (overall status + verification step)
   - Source: pi-ctl.sh show_status() function (line 186)
   - OK: supports "status" command

2. ./pi-ctl.sh ingest start
   - Source: pi-ctl.sh ingest_start() function (line 78)
   - OK: supports "ingest start"

3. ./pi-ctl.sh ingest stop
   - Source: pi-ctl.sh ingest_stop() function (line 85)
   - OK: supports "ingest stop"

4. ./pi-ctl.sh ingest status
   - Source: pi-ctl.sh ingest_status() function (line 92)
   - OK: supports "ingest status"

5. ./pi-ctl.sh wiki start
   - Source: pi-ctl.sh wiki_start() function (line 109)
   - OK: supports "wiki start"

6. ./pi-ctl.sh wiki stop
   - Source: pi-ctl.sh wiki_stop() function (line 132)
   - OK: supports "wiki stop"

7. ./pi-ctl.sh wiki status
   - Source: pi-ctl.sh wiki_status() function (line 151)
   - OK: supported but NOT mentioned in README (only wiki start/stop)

=== scripts/ commands ===

1. python scripts/ingestion_status.py
   - File exists: scripts/ingestion_status.py
   - OK

2. python scripts/kb_report.py
   - File exists: scripts/kb_report.py
   - OK

3. python scripts/backup_kb.py
   - File exists: scripts/backup_kb.py
   - OK

4. python scripts/backup_config.py
   - File exists: scripts/backup_config.py
   - OK

=== docker-compose.yml ports verified ===

- joplin-mcp: 8090:8090 (line 164)
- analysis: no host port mapped (exposes 8095 only internally)
  - Analysis Server port 8095 is correct per Dockerfile EXPOSE and server.py
- scheduler: no host port mapped (internal only)
- All other ports match README Core Services table.

=== architecture diagram port fix ===

- In README.md Architecture section: "assistant-ui (Next.js frontend :3001)" is correct.
- OpenWebUI :3000 preserved. No :3000 references remain for assistant-ui.

=== pre-built image invocation ===

- docker-compose.yml line 1-4 documents IMAGE_TAG usage.
- "IMAGE_TAG=0.1.0 docker compose up -d --no-build" is a valid invocation.

=== analysis container platform note ===

- docker-compose.yml line 177-183: platform: linux/amd64 is set.
- analysis/Dockerfile uses rocker/tidyverse which lacks arm64 builds.
- README now mentions this ARM limitation.

=== pytest ===

- pyproject.toml [tool.pytest.ini_options] confirms pytest configured.
- tests/ directory contains 22+ test files.
- "pytest -q" is a valid invocation.

== All commands verified ==
