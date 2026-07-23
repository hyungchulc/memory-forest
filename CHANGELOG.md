# Changelog

All notable changes to Memory Forest are documented in this file. The project follows [Semantic Versioning](https://semver.org/) once a version is released.

## Unreleased

### Added

- scheduler-neutral automation guide with explicit deterministic-maintenance and semantic-promotion boundaries
- runnable lock-protected maintenance wrapper plus POSIX cron, macOS launchd, and Codex Scheduled Task examples
- future-state automation diagram showing the implemented index lane and the external reviewed-promotion lane
- regression checks for automation templates, wrapper behavior, and self-contained SVG assets
- Memory Forest Retrieve integration profile with a metadata-only per-turn gate, advisory system prompt, companion skill, and regression tests

### Changed

- retrieval diagram now uses ASCII-only visible labels and an explicit cross-platform font stack to avoid missing-glyph rendering

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
