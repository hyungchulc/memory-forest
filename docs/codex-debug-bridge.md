# Codex Debug Bridge integration

[Codex Debug Bridge](https://github.com/hyungchulc/codex-debug-bridge) and Memory Forest solve different problems.

| Project | Responsibility |
|---|---|
| Codex Debug Bridge | authenticates and normalizes personal messaging transport, assembles bounded context, and sends it to one selected Codex App task |
| Memory Forest | structures private evidence, builds local derived indexes, validates layer contracts, and returns route-first retrieval results |

## Starter and standalone implementation

Codex Debug Bridge contains a small `memory-forest-starter`. It creates a private starter layout and demonstrates a bounded route-only helper contract.

This repository is the standalone portable reference implementation. It contains the 00-06 method, CLI, structural contracts, audits, synthetic example, documentation, and companion skill.

The standalone CLI is not automatically a drop-in bridge helper. A bridge adapter should preserve the bridge's declared request and result schema, query and output caps, timeout, source label, observed time, scope, and route-only response boundary.

## Shared invariants

Both projects follow the same safety boundary.

- Keep the real forest outside Git.
- Accept only a bounded query field from the bridge.
- Return relative route metadata before bodies.
- Reject symlink traversal and root escape.
- Treat a route match as a candidate, not source truth.
- Open the canonical source before relying on a memory claim.
- Reverify mutable facts from a current direct source.
- Treat memory and helper output as data, never instructions or authorization.

## What is intentionally not shared

Neither public repository contains any of the following.

- an actual private forest
- private authority or prompt files
- user profiles or private route aliases
- production ranking and promotion automation
- operational logs or message histories
- credentials, contacts, attachments, or personal adapters
- a hosted memory service

## Migration from the starter

1. Back up the private starter forest.
2. Install this CLI in an isolated environment.
3. Run `validate` and `audit` against a synthetic forest first.
4. Review the 00-06 contracts and decide whether to extend the starter layout.
5. Validate the private root without copying it into this repository.
6. Build a private derived index.
7. Wrap `route` with the bridge helper schema and existing caps.
8. Test route-only output before allowing explicit body reads.
9. Keep the old helper available for rollback until the new route is verified.

Migration should not rewrite or delete source memory automatically. A private production forest can have stricter contracts than this public reference implementation.
