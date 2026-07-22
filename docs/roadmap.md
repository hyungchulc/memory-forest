# Roadmap

Memory Forest is an alpha reference implementation. The roadmap describes direction, not delivery commitments.

## v0.1 scope

- portable 00-06 filesystem contracts
- deterministic local initializer
- structural validation and audit commands
- local SQLite derived index
- route-first and metadata-first retrieval
- explicit body inclusion boundary
- public-release privacy audit
- synthetic example forest
- standalone documentation and companion skill

## Candidate next steps

### Retrieval evaluation

- implement root-first hierarchy traversal beyond the v0.1 flat route index
- publish synthetic routing fixtures and repeatable quality metrics
- expand multilingual and mixed-language query coverage
- measure root-first routing separately from body ranking
- add conflict and stale-source evaluation cases

### Derived graph views

- generate a read-only graph from canonical paths and adjacent-layer links
- visualize domains, branches, leaves, and promotion provenance
- keep lateral similarity and community edges outside canonical Markdown
- make graph generation optional and rebuildable

### Extensible ranking

- define a provider-neutral ranker interface
- retain deterministic lexical routing as the baseline
- allow optional local or remote ranking adapters without changing canonical storage
- record ranker provenance and evaluation results

### Maintenance and migration

- incremental integrity checks
- bounded migration helpers for earlier starter layouts
- export and restore manifests
- conflict-aware promotion assistance with explicit review

### Ecosystem adapters

- documented helper adapters for agent runtimes
- editor and knowledge-tool integrations that preserve the local source boundary
- machine-readable route result schema with versioned compatibility tests

## Non-goals

The roadmap does not include a requirement to become a hosted memory service, a silent background surveillance system, an authorization engine, or an automatic truth oracle. Integrations that need those capabilities must provide their own explicit governance and user consent.
