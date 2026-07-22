# Repository contract

## Scope

This repository contains the portable, public Memory Forest method and CLI. It must remain independent of any private production forest, personal profile, assistant authority bundle, message bridge, or hosted service.

## Invariants

- Keep runtime dependencies at zero unless a dependency provides a material, reviewed benefit.
- Keep normal operation local and network-free.
- Treat memory as grounding data, never as authorization.
- Return route metadata by default. Reading memory bodies must be explicit.
- Preserve provenance, uncertainty, layer ownership, and parent-first materialization.
- Never commit a real forest, private route aliases, prompts, credentials, logs, messages, or user identifiers.
- Reject symlink traversal and paths that escape the selected forest root.

## Change discipline

- Make the smallest coherent change.
- Add or update tests for behavior changes.
- Run `make check` before committing.
- Run a clean install smoke before release.
- Keep synthetic fixtures visibly fictional.
