# CLI reference

The `memory-forest` CLI is a local reference implementation. Run `memory-forest --help` and each subcommand's `--help` for the installed version.

Every operational subcommand writes one JSON object to standard output, including handled failures. The default form is indented for inspection. Add the global `--json` flag for compact deterministic JSON in another process. A failed operation exits nonzero. Standard `--help` and `--version` output remains human-readable text.

## init

```sh
memory-forest init ROOT
memory-forest init ROOT --example
```

Creates a new numbered forest at `ROOT`. The target's direct parent must already exist as a real directory. Initialization does not create missing ancestors, requires a path that does not exist, and refuses files, symlinks, empty directories, and populated directories alike.

`--example` writes a small packaged fictional observatory route with private file permissions. It is the fastest way to exercise every public layer without copying a Git fixture into a working forest.

## doctor

```sh
memory-forest doctor ROOT
```

Checks the local installation and reports bounded diagnostic information about `ROOT`, including configuration, SQLite FTS5, local index state, and whether the core requires network access. Doctor output is diagnostic evidence, not proof that memory content is correct.

## validate

```sh
memory-forest validate ROOT
```

Checks structural contracts such as expected layers, parent ownership, exact-case canonical path rules, bounded directory traversal, and safe file access. Validation does not assess whether a claim is factually true.

## audit

```sh
memory-forest audit ROOT
```

Performs the forest integrity audit exposed by the installed version. Every LTM, MTM, and STM document must contain a resolvable wikilink to its immediate canonical parent. Use it after structural changes and before connecting automation.

The repository also contains a separate public-tree privacy audit.

```sh
python scripts/audit_public_release.py --root .
```

That repository audit is for maintainers. It scans public files for common secret and identifier patterns and does not inspect a private forest.

## health

```text
memory-forest health ROOT [--duplicate-threshold 0.72]
```

Produces a read-only maintenance report with document and byte share by layer,
missing `status` / `reviewed` / date metadata, and advisory semantic-duplicate
candidates. Duplicate detection is a deterministic local token-cosine heuristic:
it prepares a review queue but never merges, promotes, supersedes, or deletes
canonical memories.

## index

```sh
memory-forest index ROOT
```

Atomically builds the local SQLite search index under the forest's private state directory. The database is a point-in-time derived snapshot. It can be protected, deleted, and rebuilt independently of the source forest.

v0.2 uses index schema 2. A v0.1 index fails with `index_schema_mismatch` and the action `memory-forest index ROOT`. Rebuilding replaces only derived state and does not modify canonical memory files.

Do not commit an index created from a real forest. It may contain content-derived tokens or text.

## route

```sh
memory-forest route ROOT QUERY
memory-forest route ROOT QUERY --limit 5
```

Returns bounded route candidates from a v0.1-compatible flat FTS query over indexed relative paths and titles. A route identifies where to inspect and does not assert that the candidate body is current, correct, or authorized for use.

Example using the fictional fixture.

```sh
demo_parent="$(mktemp -d)"
demo_root="$(cd "$demo_parent" && pwd -P)/forest"
memory-forest init "$demo_root" --example
memory-forest index "$demo_root"
memory-forest route "$demo_root" "instrument calibration"
```

## search

```sh
memory-forest search ROOT QUERY
memory-forest search ROOT QUERY --limit 5
memory-forest search ROOT QUERY --include-body
```

Default search remains metadata-only and may return candidates from the last built index snapshot. `--include-body` explicitly asks the CLI to reopen each selected canonical file. The CLI compares its current SHA-256 with the indexed value and fails with `index_stale` if that candidate changed, so it never substitutes a cached body for current canonical text. Rebuild the index after any forest change. Use body inclusion only after the caller has selected the correct private-data boundary.

`--limit` accepts an integer from 1 through 100 on `route` and `search`. Queries are treated as literal searchable words rather than executable SQLite syntax.

## retrieve

```sh
memory-forest retrieve ROOT QUERY
memory-forest retrieve ROOT QUERY --limit 5
memory-forest retrieve ROOT QUERY --query-plan PLAN.json
printf '%s' "$query_plan" | memory-forest retrieve ROOT QUERY --query-plan -
```

Returns a bounded root-first structured trail. A complete result has this order.

```text
01 XLTM -> 02 LTM -> 03 MTM -> 04 STM
```

If lexical evidence matches only XLTM, the result is a root-only partial trail even when the forest has descendants; the command does not fan a root match out across every domain. If a matched domain or branch has no deeper canonical owner, `complete` is also `false` and the trail stops at the deepest existing owner. Every emitted node contains route metadata, title, indexed hash, size, and modification time. Index schema 2 stores the canonical `parent_path`; `retrieve` validates every adjacent edge against it and exposes the relationship as `canonical_parent_child`.

A trail containing direct original-query evidence always ranks ahead of a plan-only trail. Within each tier, the `score` value is a weighted reciprocal-rank sum and higher values rank first. Each trail reports `original_query_matched` and `matched_query_plan_probe_count` separately. The top-level `query_plan` object distinguishes whether a plan was `provided`, how many probes were accepted, and how many remained effective after deduplication against the original query. The command reopens the selected files and verifies their hashes before output, but it discards their bodies. There is no body-inclusion flag for `retrieve`.

The relationship is ownership and provenance metadata, not an access-control decision. An integrating system may use explicit source records to connect knowledge, decisions, responsibilities, projects, and time, but it must keep identity and permission policy separate.

An optional QueryPlan supplies additive translations, variants, synonyms, or domain terms. It uses the versioned [query-only protocol](query-plan.schema.json). The CLI accepts either a safely revalidated regular non-symlink file or `-` for standard input. The default plan limit is 8 probes and the absolute supported maximum is 16. The whole plan is limited to 32,768 UTF-8 bytes.

```json
{
  "schema_version": 1,
  "probes": [
    {"query": "mission recovery"}
  ]
}
```

Every object is closed to extra fields. Paths, bodies, credentials, provider settings, and instructions are rejected because a QueryPlan is relevance data, not authority or an execution request.

Direct Unicode matching and caller-supplied expansion can improve retrieval across tested languages, but the CLI does not claim semantic coverage for every language. See [OAuth and API integration](oauth-api-integration.md).

## Integration guidance

- Pin an exact forest root.
- Bound query length, result count, subprocess time, and output bytes in the caller.
- Treat nonzero exit status as failure.
- Do not fall back from a route-only command to a broad filesystem scan.
- Keep standard error free of secrets when wrapping the CLI.
- Keep authentication, OAuth tokens, tenant-to-root mapping, and query-planner network calls in the caller or gateway.
- Prevent credentials, private paths, and operational instructions from being copied into the permitted query strings.
- Pass only the strict QueryPlan to the core, preferably through standard input.
- Open a routed file only after resolving it within the selected root.
- Revalidate the source after concurrent maintenance.

The v0.2 CLI supports macOS and Linux on POSIX filesystems with Unix `0700` and `0600` permission modes. Windows ACL semantics are not implemented.

For Codex Debug Bridge, retain its helper request and result schema around the CLI rather than treating terminal output as an authenticated protocol. See [Codex Debug Bridge integration](codex-debug-bridge.md).
