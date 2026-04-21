# Roadmap

## Next Version Priorities

1. Multi-user support end to end.
2. Stronger tenant/user isolation for memory and retrieval.
3. User-aware Joplin sync routing and notebook partitioning.
4. Improved guardrails that preserve flexibility while enforcing execution contracts only when explicitly requested.

## Multi-User Direction

Planned scope:
- per-user identity propagation across OpenWebUI, pipelines, agent, and storage layers,
- user-scoped memory and knowledge retrieval defaults,
- optional org-shared spaces with explicit access controls,
- operational tooling for user onboarding and key rotation.

## Joplin Frontend Integration Direction

Planned upgrades:
- tighter notebook-to-user mapping,
- clearer sync health and conflict visibility,
- richer artifact publishing (reports/charts) back into notebook targets.

## Cloud and Deployment Evolution

- Hardened first-class deployment path on GCP (Cloud Run + Cloud SQL + managed secrets).
- Equivalent blueprints for AWS and Azure.
- Continued support for VM + managed Postgres patterns.
