# Examples

The examples in this directory are synthetic and safe to publish. They demonstrate structure and provenance, not factual knowledge or a production dataset.

## Synthetic forest

[synthetic-forest](synthetic-forest/INDEX.md) contains one fictional route across every numbered layer from `06 istm` through `01 xltm`, plus a `00 life_archive` record.

A Git checkout is intentionally shareable and normally uses `0755` directories and `0644` files. A working forest is private. Use `init --example` to create a private runnable copy of this same fictional route.

```sh
demo_parent="$(mktemp -d)"
demo_root="$(cd "$demo_parent" && pwd -P)/forest"
memory-forest init "$demo_root" --example
memory-forest validate "$demo_root"
memory-forest audit "$demo_root"
memory-forest index "$demo_root"
memory-forest route "$demo_root" "instrument calibration"
memory-forest search "$demo_root" "reference lamp"
```

Remove the temporary forest when you are finished. Do not replace this fixture with real memory. New examples must remain visibly fictional and pass the repository public-release audit.
