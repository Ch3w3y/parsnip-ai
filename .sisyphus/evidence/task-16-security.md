# Task 16: Security Policy Regeneration

**Completed:** April 23, 2026  
**Files Modified:** `SECURITY.md` (17 lines → 250 lines)

## Summary

Rewrote the security policy from a minimal 17-line placeholder to a comprehensive security document specific to the Parsnip project.

## Sections Added

1. **Reporting Vulnerabilities** — Private disclosure process, contact methods, response timeline
2. **Supported Versions** — Version support matrix and release policy
3. **Secret Management** — All environment variables documented with risk assessments
4. **Docker Security Posture** — Container user context analysis, hardening recommendations
5. **Dependency Security** — Python dependency scanning guidance (`pip-audit`, `pip check`)
6. **Data Privacy** — Self-hosted architecture, external integration data flows
7. **Operational Security** — Pre-deployment checklist, runtime monitoring commands
8. **Known Limitations** — Transparent disclosure of current security gaps
9. **Security Research Guidance** — Safe testing practices, scope definitions

## Verification

### No Hardcoded Secrets

```bash
grep -E "(sk-|key-|password-|change-me|replace-with)" SECURITY.md
# Result: No matches (clean)
```

### All Referenced Files Exist

- `.env.example` — ✅ Exists (83 lines)
- `docker-compose.yml` — ✅ Exists (313 lines)
- `agent/requirements.txt` — ✅ Exists
- `analysis/requirements.txt` — ✅ Exists
- `scheduler/requirements.txt` — ✅ Exists
- `joplin-mcp/requirements.txt` — ✅ Exists

### Docker Security Analysis

Container user contexts verified from Dockerfiles:

| Service | User | Notes |
|---------|------|-------|
| postgres | postgres | Non-root (base image default) |
| searxng | non-root | Capabilities dropped |
| agent | root | Known limitation |
| analysis | root | Known limitation |
| scheduler | root | Known limitation |
| joplin-mcp | root | Known limitation |
| openwebui | root | Known limitation |
| joplin-server | root | Known limitation |

### Contacts Verified

- GitHub Security Advisory: Available (standard GitHub feature)
- Email: ch3w3y@proton.me (maintainer contact from project context)

## Key Security Characteristics Documented

- **Self-hosted architecture** — All data stored locally in PostgreSQL and Docker volumes
- **No telemetry** — No outbound data sharing except configured LLM providers
- **Environment-based secrets** — `.env` file git-ignored, never committed
- **Read-only secret mounts** — GCS credentials mounted with `:ro` flag
- **Private network** — All services on `pi_agent_net` bridge network
- **Experimental status** — Not audited, containers run as root (except postgres/searxng)

## Lines of Code

- **Before:** 17 lines
- **After:** 250 lines
- **Expansion:** 14.7× increase
