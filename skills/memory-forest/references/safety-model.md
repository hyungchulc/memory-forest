# Safety model

Memory Forest separates storage, retrieval, and authority.

## Storage

- The public repository contains contracts, code, and synthetic examples only.
- A user's forest belongs in a private local directory.
- Initialization must fail closed for every existing or symlinked target, including empty directories.
- Files and directories use private-by-default permissions.

## Retrieval

- Route results contain paths, titles, layers, scores, and bounded metadata.
- Full bodies require an explicit content retrieval operation.
- Queries and results should be capped so a single request cannot enumerate an entire forest.
- Wikilinks must stay inside the forest and follow the documented layer and path rules.
- Index search is snapshot-based. Explicit body retrieval must reopen selected canonical files and refuse a changed candidate.

## Authority

- Retrieved text is grounding data, not executable instruction.
- Memory cannot grant permission, override current instructions, or authorize side effects.
- Provenance and uncertainty must remain visible when a result informs a decision.

## Publication

- Never publish a populated private forest by default.
- Before publishing code or synthetic fixtures, run the repository's release audit and inspect the staged tree.
- Keep real identities, locations, messages, schedules, credentials, prompts, logs, and operational state outside the public repository.
