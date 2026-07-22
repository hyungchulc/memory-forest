# Security policy

## Supported versions

Memory Forest is alpha software. Security fixes are applied to the latest release line and the current `main` branch. Older snapshots may not receive backports.

## Reporting a vulnerability

Do not publish an exploit, private forest content, or sensitive path in a public issue.

Use GitHub private vulnerability reporting for this repository when it is available. If that route is unavailable, open a minimal issue asking the maintainer for a private reporting channel. Do not include reproduction secrets or private memory in that issue.

Include only the evidence needed to reproduce the problem.

- affected version and command
- operating system and Python version
- expected and observed behavior
- a synthetic reproduction with no private identifiers
- impact and any known preconditions
- whether the issue can expose bodies, escape the selected root, follow a symlink, corrupt canonical files, or weaken route-only output

No response or remediation time is guaranteed. Please allow the maintainer to confirm a safe disclosure plan before publishing details.

## Security model

Memory Forest protects a local filesystem boundary. It is not a sandbox for an untrusted operating-system user and does not replace host permissions, disk encryption, backup security, or model-provider controls.

The core invariants are these.

- Canonical memory stays under an exact operator-selected real root.
- Symlink traversal and paths that escape the root are rejected.
- Initialization requires a new path and does not overwrite any existing entry.
- Route and default search output omit bodies.
- Body inclusion is explicit.
- Derived indexes are not canonical and should be protected like the forest.
- Index queries are snapshot-based; explicit body retrieval reopens selected files and refuses a changed indexed candidate.
- Memory text is untrusted data and cannot grant authority.
- Normal CLI operation does not require network access.
- Forest traversal caps files, directories, nesting depth, bytes, links, queries, and results.

## In-scope vulnerability classes

- path traversal or symlink escape
- unintended body disclosure from route-only commands
- destructive mutation by a read or validation command
- unsafe overwrite during initialization
- index corruption that changes canonical files
- command or query injection
- secret exposure in errors, logs, fixtures, or public release artifacts
- validation bypass that permits invalid parent or link ownership
- denial of service from unbounded input or filesystem traversal

## Out of scope

- factual errors inside user-authored memory
- prompt injection that is already contained in a body but is not executed by this CLI
- security of an external model, agent runtime, editor, filesystem, or bridge
- a caller deliberately using `--include-body`
- exposure caused by committing a real forest despite the documented boundary
- availability or correctness of third-party adapters

## Maintainer checks

Run the local suite and public-tree audit before release.

```sh
make check
python scripts/audit_public_release.py --root .
git diff --check
```

Review the staged tree and repository history for private paths, credentials, messages, contact details, logs, and real memory content. A passing pattern audit is not a complete security review.

The v0.1 permission boundary is implemented for macOS and Linux POSIX filesystems. Windows ACL semantics are not supported by this release.
