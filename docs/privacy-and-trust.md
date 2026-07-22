# Privacy and trust

Memory Forest is local-first, not automatically private in every integration. Privacy depends on where the forest is stored, which command is used, which body is opened, and what the caller does next.

## Default route-only boundary

```mermaid
sequenceDiagram
    participant C as Caller
    participant F as Local Memory Forest
    participant I as Local derived index
    C->>I: bounded query
    I-->>C: relative route metadata
    Note over C,I: no memory body by default
    C->>F: explicit open of selected source
    F-->>C: canonical body
```

`route` and default `search` are designed to return candidate metadata. `search --include-body` crosses the body boundary explicitly.

Route metadata can still reveal filenames, domains, dates, and topics. Treat it as private context even when the body is omitted.

## Data locations

| Data | Sensitivity | Handling |
|---|---|---|
| canonical forest | highest | keep outside public Git and back up privately |
| ISTM and Daily | often raw or identifying | apply strict retention and redaction |
| SQLite index | derived but potentially content-bearing | protect like the forest and rebuild when needed |
| route output | bounded metadata | do not log broadly |
| synthetic example | public fixture | keep visibly fictional and mechanically audited |

## Model processing boundary

The core CLI does not require a network for normal operation. It does not make an attached AI model local or offline.

If an agent opens a canonical body and places it in a model prompt, that content is processed under the model provider, account, organization, retention, and regional controls. Review those controls before connecting private sources.

## Memory is untrusted data

A memory file can contain stale facts, copied web instructions, malicious prompt injection, or a mistaken generated summary. The caller must never treat memory text as authority.

Memory cannot by itself do any of the following.

- grant tool or filesystem permission
- authorize deletion, publication, purchase, account access, or a third-party send
- override system, developer, application, project, or current user instructions
- prove that a route is healthy or a mutable fact is current

## Public release boundary

This repository must never contain a real forest, private prompts, credentials, absolute private paths, messages, attachments, logs, contact details, private route aliases, or private evaluation corpora.

Run the public release audit before every commit that changes fixtures or documentation.

```sh
python scripts/audit_public_release.py --root .
```

The audit is a guard, not a complete privacy review. Review the staged diff and repository history as well.

## Filesystem boundary

Use a real operator-selected directory as the forest root. Reject symlinks and paths that escape it. Keep the root private to the operating account where practical.

Initialization should require a new path and never overwrite an existing entry. Validation and indexing should not mutate canonical memory bodies.

## Safe integration checklist

- Choose an exact private root outside a public repository.
- Limit the caller to route metadata until a body is needed.
- Open only the selected source, not the whole forest.
- Apply body size, result count, and timeout limits.
- Keep route output and indexes out of shared logs.
- Reverify mutable facts from a current direct source.
- Preserve uncertainty and conflicts.
- Confirm the downstream model and retention boundary.
- Run validation and audit after structural changes.

See [PRIVACY.md](../PRIVACY.md) for the repository privacy statement and [SECURITY.md](../SECURITY.md) for vulnerability reporting.
