# Memory Forest Retrieve example

This directory contains a caller-owned, metadata-only turn gate for an agent
that must consult Memory Forest before responding.

- `turn-gate.py` reads one current user turn from standard input and runs both
  `route_index` and `retrieve_index`.
- `system-prompt.md` is a copyable advisory contract for the model.

Hard enforcement belongs in the host. Register the script as a pre-response
hook and require its successful receipt for the current turn. A prompt or skill
alone cannot prove invocation.

## Example

```sh
demo_parent="$(mktemp -d)"
demo_root="$(cd "$demo_parent" && pwd -P)/forest"
memory-forest init "$demo_root" --example
memory-forest index "$demo_root"

printf '%s' 'reference lamp' |
  python3 examples/memory-forest-retrieve/turn-gate.py "$demo_root"
```

`reference lamp` is useful for testing because it exists in document text but
not in a route path or title. The route phase can return zero while retrieve
still returns a validated structured trail.

The gate does not open bodies in its output, auto-index, repair, scan a parent
directory, or use the network. Treat its metadata as private.
