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

Performs the forest integrity audit exposed by the installed version. In v0.1, every LTM, MTM, and STM document must contain a resolvable wikilink to its immediate canonical parent. Use it after structural changes and before connecting automation.

The repository also contains a separate public-tree privacy audit.

```sh
python scripts/audit_public_release.py --root .
```

That repository audit is for maintainers. It scans public files for common secret and identifier patterns and does not inspect a private forest.

## index

```sh
memory-forest index ROOT
```

Atomically builds the local SQLite search index under the forest's private state directory. The database is a point-in-time derived snapshot. It can be protected, deleted, and rebuilt independently of the source forest.

Do not commit an index created from a real forest. It may contain content-derived tokens or text.

## route

```sh
memory-forest route ROOT QUERY
memory-forest route ROOT QUERY --limit 5
```

Returns bounded route candidates from a flat FTS query over indexed relative paths and titles. It does not traverse XLTM, LTM, MTM, and STM as a hierarchy in v0.1. A route identifies where to inspect and does not assert that the candidate body is current, correct, or authorized for use.

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

## Integration guidance

- Pin an exact forest root.
- Bound query length, result count, subprocess time, and output bytes in the caller.
- Treat nonzero exit status as failure.
- Do not fall back from a route-only command to a broad filesystem scan.
- Keep standard error free of secrets when wrapping the CLI.
- Open a routed file only after resolving it within the selected root.
- Revalidate the source after concurrent maintenance.

The v0.1 CLI supports macOS and Linux on POSIX filesystems with Unix `0700` and `0600` permission modes. Windows ACL semantics are not implemented.

For Codex Debug Bridge, retain its helper request and result schema around the CLI rather than treating terminal output as an authenticated protocol. See [Codex Debug Bridge integration](codex-debug-bridge.md).
