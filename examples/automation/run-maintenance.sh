#!/bin/sh
set -eu

umask 077

if [ "$#" -ne 1 ]; then
    printf '%s\n' "usage: run-maintenance.sh /absolute/path/to/private-forest" >&2
    exit 64
fi

forest_root=${1%/}
case "$forest_root" in
    /*) ;;
    *)
        printf '%s\n' "error: the forest root must be absolute" >&2
        exit 64
        ;;
esac

if [ ! -d "$forest_root" ] || [ -L "$forest_root" ]; then
    printf '%s\n' "error: the forest root must be a real directory" >&2
    exit 66
fi

memory_forest_bin=${MEMORY_FOREST_BIN:-memory-forest}
if ! command -v "$memory_forest_bin" >/dev/null 2>&1; then
    printf '%s\n' "error: MEMORY_FOREST_BIN is not executable" >&2
    exit 69
fi

lock_path="${forest_root}.maintenance.lock"
if ! mkdir "$lock_path" 2>/dev/null; then
    printf '%s\n' "error: could not acquire the external maintenance lock" >&2
    exit 75
fi

cleanup() {
    rmdir "$lock_path" 2>/dev/null || :
}

trap cleanup 0
trap 'exit 130' 1 2 15

"$memory_forest_bin" --json index "$forest_root"
"$memory_forest_bin" --json doctor "$forest_root"
