# Contributing

Thank you for helping improve Memory Forest. The project values small, verifiable changes and a strict public-data boundary.

## Before starting

Open an issue before a large architecture change, new dependency, file-format change, or compatibility break. Small fixes and documentation improvements can go directly to a pull request.

Security vulnerabilities should follow [SECURITY.md](SECURITY.md), not a public issue.

## Development setup

```sh
git clone https://github.com/hyungchulc/memory-forest.git
cd memory-forest
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
make check
```

Use Python 3.11 or newer. Keep runtime dependencies at zero unless a reviewed dependency provides a material benefit that the standard library cannot reasonably provide.

## Change rules

- Keep canonical memory separate from derived indexes.
- Keep normal CLI operation local and network-free.
- Return route metadata before bodies.
- Make body inclusion explicit.
- Preserve provenance, uncertainty, parent ownership, and adjacent-layer promotion.
- Reject symlink traversal and paths outside the selected forest root.
- Never make a read command mutate canonical content.
- Add or update tests for behavior changes.
- Update documentation when a command or contract changes.

## Synthetic fixtures only

Never contribute a real forest or a lightly redacted copy. Fixtures must be visibly fictional and must not include real contacts, organizations, messages, locations, account identifiers, credentials, private paths, or operational logs.

Use simple deterministic identifiers such as `evt-demo-0001`. Avoid identifiers copied from production systems.

Run the public-tree audit.

```sh
python scripts/audit_public_release.py --root .
```

Then review the full diff manually. Pattern matching does not catch every privacy failure.

## Validation

Run these checks before submitting.

```sh
make check
git diff --check
```

For a CLI behavior change, also perform a clean initialization in a temporary directory, run `doctor`, `validate`, `audit`, `index`, `route`, and metadata-only `search`, then verify that no existing target is overwritten.

## Pull requests

A useful pull request explains the problem, the chosen boundary, the verification performed, and any remaining limitation. Keep unrelated changes separate.

Confirm that the pull request contains no private data and does not weaken route-only behavior. Maintainers may ask for a smaller patch or a synthetic regression fixture.

## Documentation style

- Prefer direct claims that can be verified from this repository.
- Distinguish current behavior from roadmap ideas.
- Do not describe Memory Forest as an infallible memory, hosted service, authorization engine, or fully offline AI system.
- Use relative example paths and fictional data.
- Explain evidence limits and external processing boundaries.

## Community

Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
