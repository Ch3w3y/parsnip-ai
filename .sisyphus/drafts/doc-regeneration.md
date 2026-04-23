# Draft: Documentation Deep Regeneration

## Context
- Previous session performed surface-level fixes on 6 markdown docs.
- Architecture has changed significantly (assistant-ui frontend, PG-direct Joplin access, SourceRegistry, connection pools, embed routing, circuit breakers, stuck-job recovery, Docker bridge networking).
- User wants a **proper, deep regeneration** of documents, inspired by old content but fundamentally rewritten to reflect the current system.

## Decisions Needed
- [ ] Exact document scope (which files?)
- [ ] Target audience(s)
- [ ] Style/guidelines
- [ ] What old elements to preserve vs. discard
- [ ] Verification strategy (do commands in docs actually work?)

## In Scope (Tentative)
- README.md
- docs/ARCHITECTURE_VISUALS.md
- docs/DEPLOYMENT.md
- docs/CONFIGURATION.md
- docs/EXTENDING.md
- agent/README.md
- Possibly others (API docs, CONTRIBUTING.md, CHANGELOG integration)

## Out of Scope (TBD)
- Source code comments/JSDoc (unless requested)
- External documentation sites
