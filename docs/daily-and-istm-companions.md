# Daily and ISTM companion projects

Memory Forest defines what the `05 daily` and `06 istm` layers own, but the
core CLI deliberately does not collect conversations, read Apple application
data, or compact a private source stream. Those jobs belong to small,
source-specific companion projects.

## Companion boundaries

| Project | Owns | Does not own |
|---|---|---|
| [Codex ISTM for macOS](https://github.com/hyungchulc/codex-istm-macos) | local Codex session ingestion, bounded Daily digests, launchd examples | Mail, Calendar, Reminders, Notification Center |
| [macOS ISTM Context](https://github.com/hyungchulc/mac-istm-context) | local Apple app snapshots and event retrieval | Codex conversations, Memory Forest promotion |
| [Memory Retrieval Lab](https://github.com/hyungchulc/memory-retrieval-lab) | synthetic retrieval evaluation, metrics, ranker adapters | canonical memory storage or private source collection |

Each project keeps private runtime data outside Git. None of them publishes a
real forest, session transcript, mailbox, notification database, reminder, or
calendar event.

## Bounded Daily contract

A long-running Daily lane needs a growth boundary as well as a provenance
boundary. The public companion implementation follows these invariants.

1. Admit only new source records with stable identifiers and hashes.
2. Render a bounded causal digest instead of copying every raw payload.
3. Keep a deterministic compact manifest that can locate every admitted source
   record in ISTM.
4. Treat a missing, malformed, duplicated, or partially written manifest as a
   hard failure.
5. Advance the source cursor only after the Daily block and its manifest verify.
6. Archive or prune older data only after the replacement and its restore
   evidence verify.
7. Keep source-specific retention limits configurable. A public example must
   not silently copy one operator's private thresholds.

These rules preserve recoverability without making an unbounded Markdown file
the second raw archive.

## Reference model profile

The reference system that motivated these projects used **GPT-5.6 Sol** with
reasoning effort **xhigh** on 2026-07-24. That profile is provenance, not a
runtime dependency. Collection, hashing, cursor commits, validation, and the
deterministic retrieval baselines work without a model. An integrator that adds
model-written summaries must document its own provider, model, retention, and
review boundary.

## macOS scope

The two ISTM collectors are macOS projects. They use local Codex session files
or Apple application data and launchd examples. Apple data access can require
Automation permission or Full Disk Access, and Notification Center storage is
an undocumented best-effort surface that can change between macOS releases.

Memory Forest itself remains portable across supported POSIX systems. The
companion repositories do not change the core filesystem or retrieval schema.
