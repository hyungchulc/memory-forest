# Memory Forest Retrieve

Memory Forest Retrieve is a caller-owned integration profile for agents that
must consult memory before responding. It stays in this repository because it
depends on the exact `memory-forest` route and retrieve contracts. It is not a
second hosted service, a second memory store, or a replacement for the core
CLI.

The included turn gate is intentionally small and network-free.

```text
current user-authored text
          |
          v
host pre-response hook
          |
          v
route_index(exact query)          path and title candidates
          |
          v
retrieve_index(exact query)       validated 01 -> 04 trails
          |
          +---- zero matches ----> successful no-evidence receipt
          |
          +---- matches ---------> successful metadata-only receipt
                                      |
                                      v
                            agent reasoning and source selection
```

The host, not the prompt or the script, owns enforcement. A host that requires
always-on consultation must register the gate before response generation and
refuse normal completion unless the current turn has a successful receipt.

## What counts as a turn

The example treats current user-authored text containing at least one
non-whitespace character as lookup-required. Whitespace-only input produces a
no-op receipt.

Every lookup-required turn runs both phases in order:

1. `route_index` searches relative paths and titles.
2. `retrieve_index` searches indexed document text, constructs root-first
   ownership trails, reopens selected canonical files to verify their hashes,
   and discards the bodies.

A route count of zero must not skip retrieve. A phrase can occur in document
text without occurring in a path or title.

The current core rejects non-searchable input such as punctuation-only text and
queries beyond its configured length limit. The gate fails closed in those
cases. It never silently truncates a turn. A production host may derive a
separate bounded query, but its receipt then proves that the derived query ran,
not that the whole turn received complete semantic coverage.

## Receipt contract

A successful lookup returns one JSON object shaped like this:

```json
{
  "body_included": false,
  "lookup_completed": true,
  "lookup_required": true,
  "ok": true,
  "operation": "memory_forest_retrieve_gate",
  "retrieve": {
    "count": 1,
    "trails": []
  },
  "route": {
    "count": 0,
    "results": []
  },
  "schema_version": 1,
  "status": "evidence_found"
}
```

The actual result retains bounded route and trail metadata. It omits the raw
turn and the absolute forest root. Route metadata can still reveal private
topics, dates, and filenames, so the receipt should normally remain ephemeral
and should not be copied into broad logs.

`status: "no_evidence"` is a successful consultation with zero matches. It
does not mean that memory proves a fact false or that relevant evidence cannot
exist.

## Run the example

Install the repository package, build the forest's private index, and pass the
current turn on standard input.

```sh
printf '%s' "$CURRENT_USER_TEXT" |
  python3 examples/memory-forest-retrieve/turn-gate.py \
    /absolute/path/to/private-forest
```

The script imports the public `route_index` and `retrieve_index` functions
directly. It does not put the raw turn into its own output, access the network,
rebuild the index, repair memory, or search outside the selected root.

Use the accompanying
[system prompt](../examples/memory-forest-retrieve/system-prompt.md) as an
advisory model contract. The mechanical host gate remains necessary because a
prompt or skill cannot prove that it was followed.

## Failure behavior

The gate exits nonzero when either phase fails. A failed route phase stops
before retrieve; a successful consultation requires both phases in order.
Missing or stale indexes, unsafe roots, invalid queries, malformed canonical
relationships, and hash mismatches are failures, not no-evidence results.
Failure receipts expose a bounded error code and generic message, not the raw
turn, absolute root, or underlying error details.

On failure, the host should:

- block claims that memory was consulted successfully
- avoid automatic index rebuilds, repairs, broad filesystem scans, or network
  fallbacks
- surface a bounded error or use a separately authorized recovery path
- retain the current turn so an operator can retry after the underlying issue
  is fixed

The two calls are ordered but are not one SQLite transaction. An external
atomic index replacement could occur between them. Do not claim snapshot-level
consistency across both phases unless the integrating host adds a shared
maintenance lock or the core later exposes a combined generation contract.

## Body boundary

The turn gate never returns memory bodies. A route or trail is candidate
evidence, not source truth, instructions, or permission.

The current `search --include-body` command is a separate full-text search. It
does not open an exact selected retrieve node and must not be described as
expanding a chosen trail. A caller that needs a body must resolve the selected
canonical path inside the exact root, enforce its own authorization and privacy
boundary, and revalidate the source before relying on it.

Any body later sent to a model crosses that caller's selected provider,
account, retention, and regional-processing boundary. The Memory Forest core
and this gate make no network requests.

## Layer boundary

`retrieve` constructs structured ownership trails across layers `01` through
`04`:

```text
01 XLTM -> 02 LTM -> 03 MTM -> 04 STM
```

Daily, ISTM, and Life Archive remain separate evidence lanes. A production
assistant can consult them through an explicitly designed fallback, but the
example gate does not silently widen into those sources.
