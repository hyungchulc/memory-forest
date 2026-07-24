# Roadmap

Memory Forest is an alpha reference implementation. The roadmap describes direction, not delivery commitments.

## v0.3 scope

- portable 00-06 filesystem contracts
- deterministic local initializer
- structural validation and audit commands
- local SQLite derived index
- route-first and metadata-first retrieval
- explicit body inclusion boundary
- public-release privacy audit
- synthetic example forest
- deterministic XLTM-to-STM root-first retrieval
- strict query-only expansion protocol
- caller-owned OAuth and API gateway boundary
- English and Korean usage documentation
- standalone documentation and companion skill
- strict provenance-bound Daily Plan and Promotion Plan v1 protocols
- local network-free `apply-daily` and `promote` transaction writers
- parent-first LTM/MTM/STM materialization with adjacent parent-child links
- private write receipts, exact retry idempotency, and handled-failure rollback

## Candidate next steps

### Retrieval evaluation

- keep the core retrieval contracts versioned and compatible with the separate [Memory Retrieval Lab](https://github.com/hyungchulc/memory-retrieval-lab)
- publish synthetic routing fixtures and repeatable quality metrics in the Lab
- expand measured multilingual and mixed-language evaluation beyond the current multiscript regression fixture
- measure root-first routing separately from body ranking
- add conflict and stale-source evaluation cases
- keep the public retrieval guide aligned with the deterministic core and the
  separate Lab evaluation contract

### Derived graph views

- generate read-only graph views from canonical paths and adjacent-layer links beyond the current static fictional diagram
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
- richer conflict-aware plan review helpers outside the deterministic writer

### Ecosystem adapters

- documented helper adapters for agent runtimes
- editor and knowledge-tool integrations that preserve the local source boundary
- machine-readable route result schema with versioned compatibility tests
- keep macOS Codex-only and Apple-context ISTM collection in their source-specific companion repositories

## Non-goals

The roadmap does not include a requirement to become a hosted memory service, a silent background surveillance system, an authorization engine, or an automatic truth oracle. Integrations that need those capabilities must provide their own explicit governance and user consent.
