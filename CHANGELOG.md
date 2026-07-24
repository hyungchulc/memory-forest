# Changelog

All notable changes to Memory Forest are documented in this file. The project follows [Semantic Versioning](https://semver.org/) once a version is released.

## 0.3.0 - 2026-07-24

### Added

- end-to-end retrieval guide covering untrusted query intake, strict
  QueryPlan probes, deterministic root-first trails, explicit body access,
  freshness/conflict/no-evidence handling, Daily/ISTM fallback, and external
  ranker boundaries
- regression checks that require the public retrieval guide and resolve its
  repository-local documentation links
- scheduler-neutral automation guide with explicit deterministic-maintenance and semantic-promotion boundaries
- runnable lock-protected maintenance wrapper plus POSIX cron, macOS launchd, and Codex Scheduled Task examples
- automation diagram separating caller-owned semantic review from the
  implemented lock-protected writer and index lane
- regression checks for automation templates, wrapper behavior, and self-contained SVG assets
- Memory Forest Retrieve integration profile with a metadata-only per-turn gate, advisory system prompt, companion skill, and regression tests
- companion-project guide separating Codex conversation ingestion, Apple context collection, and retrieval evaluation from the canonical core
- bounded Daily contract covering stable source identities, compact provenance, cursor commits, verified archival, and fail-closed partial states
- strict Daily Plan v1 and Promotion Plan v1 protocols with closed objects,
  bounded unique identifiers and hashes, explicit empty no-op batches, and
  semantic routes that cannot carry raw paths, layers, or operations
- local standard-library `apply-daily` and `promote` writers with a shared
  sibling maintenance lock, parent-first materialization, inert model text,
  canonical provenance markers, and append-only promoted update blocks
- deterministic Write Receipt v1 files under private derived state, exact
  retry idempotency, and rollback of canonical files and the prior index on
  handled validation, audit, indexing, or receipt-publication failure
- focused writer tests for creation order, existing leaves, idempotency,
  source and provenance failures, path safety, locking, rollback, receipts,
  no-op plans, inert text, and CLI output

### Changed

- retrieval diagram now uses ASCII-only visible labels and an explicit cross-platform font stack to avoid missing-glyph rendering
- retrieval evaluation roadmap now points to a dedicated companion Lab while keeping the core route and retrieve contracts authoritative
- package and CLI version advanced to `0.3.0`
- automation and promotion documentation now distinguishes semantic plan
  generation from the implemented deterministic canonical writer

## 0.2.0 - 2026-07-22

### Added

- deterministic `retrieve` operation that materializes validated `XLTM -> LTM -> MTM -> STM` trails and explicit canonical parent-child relationships without returning memory bodies
- strict versioned QueryPlan protocol for caller-supplied query expansion, with query-only probes and closed rejection of extra fields
- original-query-first ranking so untrusted expansion probes cannot outrank a direct user-query match
- provider-neutral OAuth and API gateway contract that keeps tokens, network access, forest-root mapping, and authorization outside the local core
- optional bounded QueryPlan configuration while preserving v0.1 forest configuration compatibility
- strict raw forest-configuration parsing with duplicate-key rejection and exact integer schema-version checks
- English and Korean documentation, a fictional multi-domain fixture, multiscript retrieval tests, and a static derived retrieval diagram

### Changed

- local index schema advanced to version 2 with explicit canonical parent paths; existing indexes fail with `index_schema_mismatch` until rebuilt with `memory-forest index ROOT`
- the synthetic runnable example now contains two fictional structured domains
- package metadata advanced to `0.2.0`

### Compatibility

- the canonical forest schema remains version 1
- `route` and `search` inputs, body boundary, and JSON result shapes remain compatible with v0.1 after rebuilding the derived index
- language bridging depends on caller-provided probes and the indexed content; this release does not claim universal semantic or cross-language retrieval

## 0.1.0 - 2026-07-22

Initial public alpha release.

### Added

- portable seven-layer Memory Forest contracts from `00 life_archive` through `06 istm`
- parent-first domain, branch, and leaf ownership model with adjacent-layer wikilinks
- exact-case canonical paths and required immediate-parent links for structured records
- bounded file, directory, depth, byte, link, query, and result traversal
- current-source hash verification for explicit indexed body retrieval
- public release auditing for sensitive path names and nested private runtime data
- local standard-library Python CLI for initialization, diagnostics, validation, audit, indexing, routing, and search
- route-first retrieval with explicit body inclusion
- local rebuildable SQLite search index
- filesystem escape, symlink, size, count, and private-permission guards
- wholly synthetic example forest
- public-release privacy and secret audit
- companion agent skill, architecture documentation, security policy, and contribution guide

### Boundary

This release does not include any real forest, private prompts, production ranking or promotion automation, operational logs, user identifiers, or private adapters.
