# Privacy statement

Memory Forest is a local software project. It is not a hosted service and this repository does not collect or receive a user's forest.

## What the project stores

The CLI stores data only in paths selected by the operator, including the canonical forest and any local SQLite index path. A real forest may contain highly sensitive personal, professional, or operational information.

The synthetic example in this repository is fictional and is intended for public tests and documentation.

## Network behavior

Normal core CLI operation is designed to be network-free. Installation tools, source hosting, package indexes, external adapters, agent runtimes, and model providers have separate network and privacy behavior.

Memory Forest does not make an attached model local. If a caller opens a memory body and submits it to a model, the provider's account, organization, retention, regional, and privacy controls apply.

## Route-only behavior

`route` and default `search` are designed to return bounded metadata rather than canonical bodies. Body access through `search --include-body` is explicit.

Metadata can still reveal topics, dates, filenames, and domain names. Protect route output and logs accordingly.

## Derived indexes

A local index may contain text, tokens, titles, paths, or other values derived from a private forest. It should be stored, backed up, shared, and deleted under the same sensitivity classification as the source data.

Deleting an index does not delete canonical memory. Rebuilding an index does not refresh the truth of a claim.

## Operator responsibilities

- Keep real forests and indexes outside public repositories.
- Apply appropriate host permissions and disk protection.
- Treat filenames and directory names as private metadata, not only file bodies.
- Limit which processes and users can read the forest.
- Use route-only retrieval until a body is necessary.
- Review model-provider controls before sending body content.
- Define retention, redaction, backup, and deletion policies for raw source layers.
- Reverify mutable facts before acting.
- Treat memory text and helper output as untrusted data.

## Public contributions

Do not submit real memory, private identifiers, private prompts, contact details, credentials, absolute private paths, operational logs, messages, attachments, or derived indexes.

Use visibly fictional examples. Run the public-release audit and manually review the diff before contributing.

## No automatic rights or authority

A stored claim, filename, layer, route score, or repeated generated statement does not grant permission or establish ownership. Integrating applications remain responsible for authentication, authorization, user consent, and side-effect confirmation.

See [Privacy and trust](docs/privacy-and-trust.md) for the architecture boundary and [SECURITY.md](SECURITY.md) for vulnerability reporting.
